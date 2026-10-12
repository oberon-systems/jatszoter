use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use serde_json::{json, Value};
use tempfile::TempDir;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixListener;
use tokio::sync::RwLock;
use url::Url;
use wiremock::matchers::{body_json, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

use super::{detail, message, run, BROKEN, MAX_DETAIL, SILENT, UNWRITTEN};
use crate::libs::agent::{Auth, Signer};
use crate::libs::api::HttpApi;
use crate::libs::state::Credential;

const ROUTE: &str = "/api/v1/agents/alpha/deployments/beta/checks/gamma";
const SCRIPT: &str = "/run/jatszoter-check-gamma.sh";
const TEXT: &str = "test -e /alpha\n";

type Seen = Arc<Mutex<Vec<Value>>>;

#[derive(Clone, Copy)]
enum Guest {
    Exits(i64, &'static str, &'static str),
    Hangs,
    RefusesToRun,
    RefusesToOpen,
}

/// A guest agent on `qga.sock`: records what it is asked, bar the sync and the status polls.
fn guest_agent(dir: &TempDir, guest: Guest) -> (PathBuf, Seen) {
    let socket = dir.path().join("qga.sock");
    let listener = UnixListener::bind(&socket).unwrap();
    let seen = Seen::default();
    let record = Arc::clone(&seen);
    tokio::spawn(async move {
        loop {
            let (stream, _) = listener.accept().await.unwrap();
            let (reader, mut writer) = stream.into_split();
            let mut reader = BufReader::new(reader);
            let mut line = Vec::new();
            let mut script = false;
            while reader.read_until(b'\n', &mut line).await.unwrap_or(0) > 0 {
                let synced = line.first() == Some(&0xFF);
                let sent: Value = serde_json::from_slice(&line[usize::from(synced)..]).unwrap();
                let command = sent["execute"].as_str().unwrap().to_owned();
                if !matches!(
                    command.as_str(),
                    "guest-sync-delimited" | "guest-exec-status"
                ) {
                    record.lock().unwrap().push(sent.clone());
                }
                let refused = json!({ "error": { "class": "GenericError", "desc": "delta" } });
                let answer = match (command.as_str(), guest) {
                    ("guest-sync-delimited", _) => json!({ "return": sent["arguments"]["id"] }),
                    ("guest-file-open", Guest::RefusesToOpen) => refused,
                    ("guest-file-open", _) => json!({ "return": 1000 }),
                    ("guest-file-write", _) => {
                        let data = sent["arguments"]["buf-b64"].as_str().unwrap();
                        let count = STANDARD.decode(data).unwrap().len();
                        json!({ "return": { "count": count, "eof": false } })
                    }
                    ("guest-exec", _) => {
                        script = sent["arguments"]["path"] == "/bin/sh";
                        if script && matches!(guest, Guest::RefusesToRun) {
                            refused
                        } else {
                            json!({ "return": { "pid": 7 } })
                        }
                    }
                    ("guest-exec-status", Guest::Hangs) if script => {
                        json!({ "return": { "exited": false } })
                    }
                    ("guest-exec-status", Guest::Exits(code, out, err)) if script => {
                        json!({ "return": {
                            "exited": true,
                            "exitcode": code,
                            "out-data": STANDARD.encode(out),
                            "err-data": STANDARD.encode(err)
                        } })
                    }
                    ("guest-exec-status", _) => {
                        json!({ "return": { "exited": true, "exitcode": 0 } })
                    }
                    _ => json!({ "return": {} }),
                };
                let mut reply = if synced { vec![0xFF] } else { Vec::new() };
                reply.extend(format!("{answer}\n").bytes());
                if writer.write_all(&reply).await.is_err() {
                    break;
                }
                line.clear();
            }
        }
    });
    (socket, seen)
}

fn auth() -> Auth {
    Arc::new(RwLock::new(Some(Signer {
        agent_id: "alpha".into(),
        credential: Credential {
            credential: "current:1:1790000000".into(),
            expires_at: 1_790_000_000,
            key: "epsilon".into(),
        },
    })))
}

fn api(server: &MockServer) -> HttpApi {
    HttpApi::new(Url::parse(&format!("{}/", server.uri())).unwrap()).unwrap()
}

/// An API that serves the script once and takes exactly the result `posted`.
async fn serving(timeout_seconds: u64, posted: Value) -> MockServer {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path(ROUTE))
        .respond_with(
            ResponseTemplate::new(200)
                .set_body_json(json!({ "script": TEXT, "timeout_seconds": timeout_seconds })),
        )
        .expect(1)
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path(ROUTE))
        .and(body_json(posted))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({})))
        .expect(1)
        .mount(&server)
        .await;
    server
}

fn error(message: &str) -> Value {
    json!({ "status": "error", "exit_code": null, "message": message, "detail": null })
}

fn exec(path: &str, arg: &[&str], capture: bool) -> Value {
    json!({
        "execute": "guest-exec",
        "arguments": { "path": path, "arg": arg, "capture-output": capture }
    })
}

fn open() -> Value {
    json!({ "execute": "guest-file-open", "arguments": { "path": SCRIPT, "mode": "w" } })
}

fn written() -> Vec<Value> {
    vec![
        open(),
        json!({
            "execute": "guest-file-write",
            "arguments": { "handle": 1000, "buf-b64": STANDARD.encode(TEXT) }
        }),
        json!({ "execute": "guest-file-close", "arguments": { "handle": 1000 } }),
    ]
}

fn started() -> Value {
    exec("/bin/sh", &[SCRIPT], true)
}

fn removed() -> Value {
    exec("/bin/rm", &["-f", "--", SCRIPT], false)
}

fn seen(seen: &Seen) -> Vec<Value> {
    seen.lock().unwrap().clone()
}

#[tokio::test]
async fn a_script_that_exits_0_posts_passed_with_its_first_line() {
    let server = serving(
        30,
        json!({ "status": "passed", "exit_code": 0, "message": "alpha is there", "detail": null }),
    )
    .await;
    let dir = tempfile::tempdir().unwrap();
    let (socket, asked) = guest_agent(&dir, Guest::Exits(0, "alpha is there\nzeta\n", ""));

    run(&api(&server), &auth(), &socket, "beta", "gamma").await;

    let mut expected = written();
    expected.extend([started(), removed()]);
    assert_eq!(seen(&asked), expected);
}

#[tokio::test]
async fn a_script_that_exits_3_posts_failed_with_the_code_and_stderr() {
    let server = serving(
        30,
        json!({ "status": "failed", "exit_code": 3, "message": null, "detail": "no alpha\n" }),
    )
    .await;
    let dir = tempfile::tempdir().unwrap();
    let (socket, asked) = guest_agent(&dir, Guest::Exits(3, "", "no alpha\n"));

    run(&api(&server), &auth(), &socket, "beta", "gamma").await;

    assert_eq!(seen(&asked).last(), Some(&removed()));
}

#[tokio::test]
async fn a_script_running_at_the_deadline_is_killed_posted_as_timeout_and_deleted() {
    let server = serving(
        1,
        json!({ "status": "timeout", "exit_code": null, "message": null, "detail": null }),
    )
    .await;
    let dir = tempfile::tempdir().unwrap();
    let (socket, asked) = guest_agent(&dir, Guest::Hangs);

    run(&api(&server), &auth(), &socket, "beta", "gamma").await;

    let mut expected = written();
    expected.extend([
        started(),
        exec("/bin/kill", &["-KILL", "7"], false),
        removed(),
    ]);
    assert_eq!(seen(&asked), expected);
}

#[tokio::test]
async fn a_guest_agent_error_posts_error_and_the_script_is_still_deleted() {
    let server = serving(30, error(BROKEN)).await;
    let dir = tempfile::tempdir().unwrap();
    let (socket, asked) = guest_agent(&dir, Guest::RefusesToRun);

    run(&api(&server), &auth(), &socket, "beta", "gamma").await;

    let mut expected = written();
    expected.extend([started(), removed()]);
    assert_eq!(seen(&asked), expected);
}

#[tokio::test]
async fn a_file_that_cannot_be_written_posts_error_and_runs_nothing() {
    let server = serving(30, error(UNWRITTEN)).await;
    let dir = tempfile::tempdir().unwrap();
    let (socket, asked) = guest_agent(&dir, Guest::RefusesToOpen);

    run(&api(&server), &auth(), &socket, "beta", "gamma").await;

    assert_eq!(seen(&asked), vec![open(), removed()]);
}

#[tokio::test]
async fn a_guest_agent_that_is_not_there_posts_error() {
    let server = serving(30, error(SILENT)).await;
    let dir = tempfile::tempdir().unwrap();

    run(
        &api(&server),
        &auth(),
        &dir.path().join("qga.sock"),
        "beta",
        "gamma",
    )
    .await;
}

#[tokio::test]
async fn a_409_on_the_script_ends_without_a_post_and_without_the_guest() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path(ROUTE))
        .respond_with(ResponseTemplate::new(409))
        .expect(1)
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .respond_with(ResponseTemplate::new(200))
        .expect(0)
        .mount(&server)
        .await;
    let dir = tempfile::tempdir().unwrap();
    let (socket, asked) = guest_agent(&dir, Guest::Exits(0, "", ""));

    run(&api(&server), &auth(), &socket, "beta", "gamma").await;

    assert!(seen(&asked).is_empty());
}

#[tokio::test]
async fn a_409_on_the_result_is_posted_once() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .respond_with(
            ResponseTemplate::new(200)
                .set_body_json(json!({ "script": TEXT, "timeout_seconds": 30 })),
        )
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path(ROUTE))
        .respond_with(ResponseTemplate::new(409))
        .expect(1)
        .mount(&server)
        .await;
    let dir = tempfile::tempdir().unwrap();
    let (socket, asked) = guest_agent(&dir, Guest::Exits(0, "", ""));

    run(&api(&server), &auth(), &socket, "beta", "gamma").await;

    assert_eq!(seen(&asked).last(), Some(&removed()));
}

#[tokio::test]
async fn a_check_id_that_is_no_file_name_asks_nobody() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .respond_with(ResponseTemplate::new(200))
        .expect(0)
        .mount(&server)
        .await;
    let dir = tempfile::tempdir().unwrap();
    let (socket, asked) = guest_agent(&dir, Guest::Exits(0, "", ""));

    run(&api(&server), &auth(), &socket, "beta", "../gamma").await;

    assert!(seen(&asked).is_empty());
}

#[test]
fn the_message_is_the_first_line_cut_to_200_characters() {
    let long = "\u{e9}".repeat(300);

    assert_eq!(message(b"alpha\r\nbeta\n").as_deref(), Some("alpha"));
    assert_eq!(message(long.as_bytes()), Some("\u{e9}".repeat(200)));
    assert_eq!(message(b"\nalpha\n"), None);
    assert_eq!(message(b""), None);
}

#[test]
fn the_detail_is_the_first_4096_bytes_of_stderr() {
    let long = "\u{e9}".repeat(MAX_DETAIL);

    let cut = detail(long.as_bytes()).unwrap();

    assert_eq!(detail(b"alpha\nbeta\n").as_deref(), Some("alpha\nbeta\n"));
    assert_eq!(cut, "\u{e9}".repeat(MAX_DETAIL / 2));
    assert_eq!(detail(&[b'a'; MAX_DETAIL + 1]).unwrap().len(), MAX_DETAIL);
    assert_eq!(detail(b""), None);
}
