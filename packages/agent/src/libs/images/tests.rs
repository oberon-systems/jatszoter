use std::fs;
use std::net::{IpAddr, TcpListener};
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use ipnet::IpNet;
use reqwest::dns::Resolve;
use sha2::{Digest, Sha256};
use tempfile::TempDir;
use url::Url;
use wiremock::matchers::{any, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

use super::{is_public, AddressRule, ImageCache, ADDRESS_REFUSED};
use crate::libs::error::AgentError;

const BODY: &[u8] = b"alpha base image";

fn nets(cidrs: &[&str]) -> Vec<IpNet> {
    cidrs.iter().map(|cidr| cidr.parse().unwrap()).collect()
}

fn rule(cidrs: &[&str]) -> AddressRule {
    AddressRule(Arc::from(nets(cidrs)))
}

async fn redirects_to(server: &MockServer, target: &str) {
    Mock::given(method("GET"))
        .and(path("/base.qcow2"))
        .respond_with(ResponseTemplate::new(302).insert_header("location", target))
        .mount(server)
        .await;
}

fn digest(body: &[u8]) -> String {
    hex::encode(Sha256::digest(body))
}

fn images(dir: &TempDir) -> PathBuf {
    dir.path().join("images")
}

fn cache(dir: &TempDir, limit: u64) -> ImageCache {
    ImageCache::new(&images(dir), limit, &nets(&["127.0.0.1/32"]), false).unwrap()
}

fn url(server: &MockServer) -> Url {
    Url::parse(&format!("{}/base.qcow2?token=beta", server.uri())).unwrap()
}

fn names(dir: &Path) -> Vec<String> {
    let mut names: Vec<String> = fs::read_dir(dir)
        .unwrap()
        .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
        .collect();
    names.sort();
    names
}

async fn serve(server: &MockServer, response: ResponseTemplate, calls: u64) {
    Mock::given(method("GET"))
        .and(path("/base.qcow2"))
        .respond_with(response)
        .expect(calls)
        .mount(server)
        .await;
}

#[tokio::test]
async fn a_download_lands_under_its_digest_private() {
    let server = MockServer::start().await;
    serve(&server, ResponseTemplate::new(200).set_body_bytes(BODY), 1).await;
    let dir = TempDir::new().unwrap();

    let image = cache(&dir, 1024)
        .ensure(&url(&server), &digest(BODY))
        .await
        .unwrap();

    assert_eq!(image, images(&dir).join(format!("{}.qcow2", digest(BODY))));
    assert_eq!(fs::read(&image).unwrap(), BODY);
    assert_eq!(
        fs::metadata(&image).unwrap().permissions().mode() & 0o777,
        0o600
    );
    assert_eq!(names(&images(&dir)).len(), 1);
}

#[tokio::test]
async fn a_cached_image_makes_no_request() {
    let server = MockServer::start().await;
    serve(&server, ResponseTemplate::new(200).set_body_bytes(BODY), 1).await;
    let dir = TempDir::new().unwrap();
    let cache = cache(&dir, 1024);

    let first = cache.ensure(&url(&server), &digest(BODY)).await.unwrap();
    let second = cache.ensure(&url(&server), &digest(BODY)).await.unwrap();

    assert_eq!(first, second);
}

#[tokio::test]
async fn concurrent_calls_share_one_download() {
    let server = MockServer::start().await;
    let slow = ResponseTemplate::new(200)
        .set_body_bytes(BODY)
        .set_delay(Duration::from_millis(200));
    serve(&server, slow, 1).await;
    let dir = TempDir::new().unwrap();
    let cache = cache(&dir, 1024);
    let (url, digest) = (url(&server), digest(BODY));

    let (first, second) = tokio::join!(cache.ensure(&url, &digest), cache.ensure(&url, &digest));

    assert_eq!(first.unwrap(), second.unwrap());
}

#[tokio::test]
async fn a_wrong_digest_leaves_no_file() {
    let server = MockServer::start().await;
    serve(&server, ResponseTemplate::new(200).set_body_bytes(BODY), 1).await;
    let dir = TempDir::new().unwrap();
    let expected = digest(b"beta base image");

    let err = cache(&dir, 1024)
        .ensure(&url(&server), &expected)
        .await
        .unwrap_err();

    let message = err.to_string();
    assert!(matches!(err, AgentError::Image(_)));
    assert!(message.contains(&expected));
    assert!(!message.contains("token"));
    assert!(names(&images(&dir)).is_empty());
}

#[tokio::test]
async fn a_body_over_the_limit_leaves_no_file() {
    let server = MockServer::start().await;
    serve(&server, ResponseTemplate::new(200).set_body_bytes(BODY), 1).await;
    let dir = TempDir::new().unwrap();

    let err = cache(&dir, 4)
        .ensure(&url(&server), &digest(BODY))
        .await
        .unwrap_err();

    assert!(matches!(err, AgentError::Image(_)));
    assert!(names(&images(&dir)).is_empty());
}

#[tokio::test]
async fn plain_http_off_loopback_is_refused() {
    let dir = TempDir::new().unwrap();
    let remote = Url::parse("http://192.0.2.1/base.qcow2").unwrap();

    let err = cache(&dir, 1024)
        .ensure(&remote, &digest(BODY))
        .await
        .unwrap_err();

    assert!(matches!(err, AgentError::Image(_)));
    assert!(names(&images(&dir)).is_empty());
}

#[tokio::test]
async fn a_redirect_outside_the_rule_never_reaches_its_target() {
    let target = MockServer::builder()
        .listener(TcpListener::bind("127.0.0.2:0").unwrap())
        .start()
        .await;
    Mock::given(any())
        .respond_with(ResponseTemplate::new(200).set_body_bytes(BODY))
        .expect(0)
        .mount(&target)
        .await;
    let server = MockServer::start().await;
    redirects_to(&server, &target.uri()).await;
    let dir = TempDir::new().unwrap();

    let err = cache(&dir, 1024)
        .ensure(&url(&server), &digest(BODY))
        .await
        .unwrap_err();

    assert!(err.to_string().contains(ADDRESS_REFUSED), "{err}");
    assert!(names(&images(&dir)).is_empty());
}

#[tokio::test]
async fn redirects_to_internal_literals_are_refused() {
    for target in [
        "https://10.0.0.1/",
        "https://[::ffff:127.0.0.2]/",
        "https://[fe80::1]/",
        "https://0.0.0.0/",
    ] {
        let server = MockServer::start().await;
        redirects_to(&server, target).await;
        let dir = TempDir::new().unwrap();

        let err = cache(&dir, 1024)
            .ensure(&url(&server), &digest(BODY))
            .await
            .unwrap_err();

        assert!(err.to_string().contains(ADDRESS_REFUSED), "{target}: {err}");
    }
}

#[tokio::test]
async fn a_first_url_outside_the_rule_makes_no_request() {
    let server = MockServer::start().await;
    serve(&server, ResponseTemplate::new(200).set_body_bytes(BODY), 0).await;
    let dir = TempDir::new().unwrap();
    let strict = ImageCache::new(&images(&dir), 1024, &[], false).unwrap();

    let err = strict
        .ensure(&url(&server), &digest(BODY))
        .await
        .unwrap_err();

    assert!(err.to_string().contains(ADDRESS_REFUSED), "{err}");
    assert!(names(&images(&dir)).is_empty());
}

#[tokio::test]
async fn a_redirect_inside_the_rule_is_followed_without_a_referer() {
    let server = MockServer::start().await;
    redirects_to(&server, "/moved.qcow2").await;
    Mock::given(method("GET"))
        .and(path("/moved.qcow2"))
        .respond_with(ResponseTemplate::new(200).set_body_bytes(BODY))
        .expect(1)
        .mount(&server)
        .await;
    let dir = TempDir::new().unwrap();

    cache(&dir, 1024)
        .ensure(&url(&server), &digest(BODY))
        .await
        .unwrap();

    let requests = server.received_requests().await.unwrap();
    assert_eq!(requests.len(), 2);
    assert!(requests[1].headers.get("referer").is_none());
}

#[test]
fn only_public_addresses_are_public() {
    for public in ["1.1.1.1", "8.8.8.8", "2606:4700:4700::1111"] {
        assert!(is_public(public.parse::<IpAddr>().unwrap()), "{public}");
    }
    for internal in [
        "0.0.0.0",
        "10.0.0.1",
        "100.64.0.1",
        "127.0.0.1",
        "169.254.169.254",
        "172.16.0.1",
        "192.0.2.1",
        "192.168.0.1",
        "198.18.0.1",
        "198.51.100.1",
        "203.0.113.1",
        "224.0.0.1",
        "255.255.255.255",
        "::",
        "::1",
        "64:ff9b::7f00:1",
        "2001::1",
        "2001:db8::1",
        "2002:7f00:1::",
        "3fff::1",
        "fc00::1",
        "fe80::1",
        "ff02::1",
    ] {
        assert!(
            !is_public(internal.parse::<IpAddr>().unwrap()),
            "{internal}"
        );
    }
}

#[test]
fn a_mapped_address_counts_as_the_address_it_maps() {
    let strict = rule(&[]);
    let listed = rule(&["127.0.0.1/32"]);
    let mapped: IpAddr = "::ffff:127.0.0.1".parse().unwrap();

    assert!(!strict.allows(mapped));
    assert!(listed.allows(mapped));
}

#[test]
fn only_a_listed_network_opens_an_internal_address() {
    let listed = rule(&["192.0.2.0/25"]);

    assert!(listed.allows("192.0.2.1".parse().unwrap()));
    assert!(!listed.allows("192.0.2.200".parse().unwrap()));
    assert!(!listed.allows("127.0.0.1".parse().unwrap()));
}

#[tokio::test]
async fn the_resolver_applies_the_rule_to_the_whole_answer() {
    let refused = rule(&[]).resolve("localhost".parse().unwrap()).await;
    let answered = rule(&["127.0.0.0/8", "::1/128"])
        .resolve("localhost".parse().unwrap())
        .await;

    assert_eq!(refused.err().unwrap().to_string(), ADDRESS_REFUSED);
    assert!(answered.unwrap().all(|addr| addr.ip().is_loopback()));
}

#[tokio::test]
async fn a_malformed_digest_is_refused_before_any_request() {
    let server = MockServer::start().await;
    Mock::given(any())
        .respond_with(ResponseTemplate::new(200))
        .expect(0)
        .mount(&server)
        .await;
    let dir = TempDir::new().unwrap();
    let cache = cache(&dir, 1024);
    let good = digest(BODY);

    let bad_digests = [
        good.to_uppercase(),
        good[1..].to_owned(),
        format!("{}g", &good[1..]),
        String::new(),
    ];
    for bad in bad_digests {
        let err = cache.ensure(&url(&server), &bad).await.unwrap_err();
        assert!(matches!(err, AgentError::Image(_)), "{bad:?}");
    }
}

#[tokio::test]
async fn a_missing_image_is_an_error() {
    let server = MockServer::start().await;
    serve(&server, ResponseTemplate::new(404), 1).await;
    let dir = TempDir::new().unwrap();

    let err = cache(&dir, 1024)
        .ensure(&url(&server), &digest(BODY))
        .await
        .unwrap_err();

    assert!(matches!(err, AgentError::Image(_)));
    assert!(!err.to_string().contains("token"));
    assert!(names(&images(&dir)).is_empty());
}

#[test]
fn stale_part_files_vanish_on_new() {
    let dir = TempDir::new().unwrap();
    fs::create_dir(images(&dir)).unwrap();
    fs::set_permissions(images(&dir), fs::Permissions::from_mode(0o700)).unwrap();
    let kept = format!("{}.qcow2", digest(BODY));
    fs::write(images(&dir).join(&kept), BODY).unwrap();
    fs::write(
        images(&dir).join(format!("{}.part-alpha", digest(BODY))),
        b"half",
    )
    .unwrap();

    cache(&dir, 1024);

    assert_eq!(names(&images(&dir)), [kept]);
}
