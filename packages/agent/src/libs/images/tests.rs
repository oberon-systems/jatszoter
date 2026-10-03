use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::time::Duration;

use sha2::{Digest, Sha256};
use tempfile::TempDir;
use url::Url;
use wiremock::matchers::{any, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

use super::ImageCache;
use crate::libs::error::AgentError;

const BODY: &[u8] = b"alpha base image";

fn digest(body: &[u8]) -> String {
    hex::encode(Sha256::digest(body))
}

fn images(dir: &TempDir) -> PathBuf {
    dir.path().join("images")
}

fn cache(dir: &TempDir, limit: u64) -> ImageCache {
    ImageCache::new(&images(dir), limit).unwrap()
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
