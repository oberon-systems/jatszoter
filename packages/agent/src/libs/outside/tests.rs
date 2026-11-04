use tokio::io::AsyncWriteExt;
use tokio::net::TcpListener;
use wiremock::matchers::method;
use wiremock::{Mock, MockServer, ResponseTemplate};

use super::{inspector_seed, judged, network, Aimed, UNREADABLE};
use crate::libs::api::{CheckStatus, DiskAssert, HttpProbe, NetworkProbe};

const GUEST_PORT: u16 = 80;

fn aimed(host_port: Option<u16>, http: Option<HttpProbe>) -> Vec<Aimed> {
    vec![Aimed {
        host_port,
        probe: NetworkProbe {
            port: GUEST_PORT,
            http,
        },
    }]
}

fn http(status: u16, contains: Option<&str>) -> Option<HttpProbe> {
    Some(HttpProbe {
        path: "/alpha".into(),
        status,
        contains: contains.map(Into::into),
    })
}

async fn answering(response: ResponseTemplate) -> (MockServer, u16) {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .respond_with(response)
        .mount(&server)
        .await;
    let port = server.address().port();
    (server, port)
}

#[tokio::test]
async fn an_answer_with_the_status_and_the_text_passes() {
    let (_server, port) = answering(ResponseTemplate::new(200).set_body_string("a beta c")).await;

    assert_eq!(
        network(&aimed(Some(port), http(200, Some("beta")))).await,
        Ok(())
    );
}

#[tokio::test]
async fn another_status_fails_and_names_the_guest_port() {
    let (_server, port) = answering(ResponseTemplate::new(502)).await;

    assert_eq!(
        network(&aimed(Some(port), http(200, None))).await,
        Err("port 80: answered 502, not 200".into())
    );
}

#[tokio::test]
async fn an_answer_without_the_text_fails() {
    let (_server, port) = answering(ResponseTemplate::new(200).set_body_string("gamma")).await;

    assert_eq!(
        network(&aimed(Some(port), http(200, Some("beta")))).await,
        Err("port 80: the answer lacks the expected text".into())
    );
}

#[tokio::test]
async fn text_past_the_bound_does_not_count() {
    let body = format!("{}beta", "a".repeat(super::MAX_BODY));
    let (_server, port) = answering(ResponseTemplate::new(200).set_body_string(body)).await;

    assert!(network(&aimed(Some(port), http(200, Some("beta"))))
        .await
        .is_err());
}

#[tokio::test]
async fn a_redirect_is_not_followed() {
    let (_server, port) =
        answering(ResponseTemplate::new(302).insert_header("Location", "http://192.0.2.1/")).await;

    assert_eq!(
        network(&aimed(Some(port), http(200, None))).await,
        Err("port 80: answered 302, not 200".into())
    );
}

#[tokio::test]
async fn a_port_that_is_not_forwarded_fails() {
    assert_eq!(
        network(&aimed(None, None)).await,
        Err("port 80: not forwarded to this VM".into())
    );
}

#[tokio::test]
async fn a_tcp_probe_passes_while_the_connection_stays_open() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let server = tokio::spawn(async move {
        let (held, _) = listener.accept().await.unwrap();
        tokio::time::sleep(super::TCP_SETTLE * 2).await;
        drop(held);
    });

    assert_eq!(network(&aimed(Some(port), None)).await, Ok(()));
    server.abort();
}

#[tokio::test]
async fn a_tcp_probe_passes_on_a_greeting() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    tokio::spawn(async move {
        let (mut held, _) = listener.accept().await.unwrap();
        held.write_all(b"SSH-2.0-alpha\r\n").await.unwrap();
        tokio::time::sleep(super::TCP_SETTLE * 2).await;
    });

    assert_eq!(network(&aimed(Some(port), None)).await, Ok(()));
}

#[tokio::test]
async fn a_tcp_probe_fails_when_the_guest_closes_at_once() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    tokio::spawn(async move {
        drop(listener.accept().await.unwrap());
    });

    assert_eq!(
        network(&aimed(Some(port), None)).await,
        Err("port 80: the guest closed the connection".into())
    );
}

fn file(path: &str, exists: bool, contains: Option<&str>) -> DiskAssert {
    DiskAssert {
        path: path.into(),
        exists,
        contains: contains.map(Into::into),
    }
}

fn verdict(asserts: &[DiskAssert], answers: &str) -> (CheckStatus, Option<String>) {
    // The answers disk is a raw file: what the script wrote, then zeros.
    let mut raw = answers.as_bytes().to_vec();
    raw.resize(raw.len() + 16, 0);
    let result = judged(asserts, &raw);
    (result.status, result.message)
}

#[test]
fn files_as_asserted_pass() {
    let asserts = [
        file("/alpha", true, Some("mu")),
        file("/beta", true, None),
        file("/gamma", false, None),
    ];

    assert_eq!(
        verdict(&asserts, "1 1\n1 0\n0 0\nend\n"),
        (CheckStatus::Passed, None)
    );
}

#[test]
fn a_missing_file_fails_and_is_named() {
    assert_eq!(
        verdict(&[file("/gamma", true, None)], "0 0\nend\n"),
        (CheckStatus::Failed, Some("/gamma is missing".into()))
    );
}

#[test]
fn a_file_that_should_be_gone_fails_and_is_named() {
    assert_eq!(
        verdict(&[file("/alpha", false, None)], "1 0\nend\n"),
        (CheckStatus::Failed, Some("/alpha is there".into()))
    );
}

#[test]
fn a_file_without_the_text_fails_and_is_named() {
    assert_eq!(
        verdict(&[file("/beta", true, Some("mu"))], "1 0\nend\n"),
        (
            CheckStatus::Failed,
            Some("/beta lacks the expected text".into())
        )
    );
}

#[test]
fn answers_that_do_not_fit_are_an_error_not_a_verdict() {
    let asserts = [file("/alpha", true, None)];
    let unreadable = (CheckStatus::Error, Some(UNREADABLE.to_owned()));

    for answers in [
        "",
        "1 0\n",
        "end\n",
        "1 0\n1 0\nend\n",
        "yes\nend\n",
        "0 1\nend\n",
    ] {
        assert_eq!(verdict(&asserts, answers), unreadable, "{answers:?}");
    }
    assert_eq!(judged(&asserts, &[0xff, b'\n']).status, CheckStatus::Error);
}

#[test]
fn the_script_asks_once_per_file_and_searches_only_when_told() {
    let seed = inspector_seed(
        "vm-alpha",
        &[
            file("/etc/alpha.conf", true, Some("beta = 1;")),
            file("/etc/gamma", false, None),
        ],
    );

    let script = &seed.user_data;
    assert!(script.starts_with("#!/bin/sh\n"));
    assert_eq!(seed.meta_data, "instance-id: inspect-vm-alpha\n");
    assert_eq!(script.matches("echo \"$there $holds\"").count(), 2);
    assert_eq!(script.matches("grep -qF").count(), 1);
    assert!(script.contains("grep -qF -- 'beta = 1;' \"$root\"'/etc/alpha.conf'"));
    assert!(script.contains("[ ! -L \"$root\"'/etc/gamma' ]"));
    assert!(script.contains("ro,norecovery,nouuid"));
    assert!(script.contains("ro,noload"));
    assert!(script.trim_end().ends_with("poweroff"));
}

#[test]
fn the_script_finds_the_disks_the_vm_names() {
    let seed = inspector_seed("vm-alpha", &[]);

    assert!(seed
        .user_data
        .contains(&format!("device {}", crate::libs::vm::DISK_SERIAL)));
    assert!(seed
        .user_data
        .contains(&format!("device {}", crate::libs::vm::ANSWERS_SERIAL)));
}

#[test]
fn a_quote_cannot_leave_its_string() {
    let seed = inspector_seed("vm-alpha", &[file("/alpha", true, Some("a'b"))]);

    assert!(seed.user_data.contains(r"'a'\''b'"));
}
