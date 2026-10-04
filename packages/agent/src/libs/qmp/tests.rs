use std::path::PathBuf;
use std::time::Duration;

use serde_json::{json, Value};
use tempfile::TempDir;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixListener;
use tokio::task::JoinHandle;

use super::Qmp;
use crate::libs::error::AgentError;

const TIMEOUT: Duration = Duration::from_secs(5);
const GREETING: &str =
    r#"{"QMP": {"version": {"qemu": {"major": 9, "minor": 2, "micro": 0}}, "capabilities": []}}"#;

/// Accepts one client and answers the n-th command it reads with the n-th batch of replies.
fn serve(
    dir: &TempDir,
    greet: bool,
    replies: Vec<Vec<Value>>,
) -> (PathBuf, JoinHandle<Vec<Value>>) {
    let path = dir.path().join("qmp.sock");
    let listener = UnixListener::bind(&path).unwrap();
    let task = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let (reader, mut writer) = stream.into_split();
        let mut lines = BufReader::new(reader).lines();
        let mut seen = Vec::new();
        if greet {
            writer
                .write_all(format!("{GREETING}\n").as_bytes())
                .await
                .unwrap();
        }
        for batch in replies {
            let Some(line) = lines.next_line().await.unwrap() else {
                break;
            };
            seen.push(serde_json::from_str(&line).unwrap());
            for reply in batch {
                writer
                    .write_all(format!("{reply}\n").as_bytes())
                    .await
                    .unwrap();
            }
        }
        while let Ok(Some(_)) = lines.next_line().await {}
        seen
    });
    (path, task)
}

fn ok() -> Value {
    json!({ "return": {} })
}

#[tokio::test]
async fn the_handshake_then_a_command_with_arguments() {
    let dir = TempDir::new().unwrap();
    let (path, server) = serve(
        &dir,
        true,
        vec![
            vec![ok()],
            vec![json!({ "return": { "status": "running" } })],
        ],
    );

    let mut qmp = Qmp::connect(&path, TIMEOUT).await.unwrap();
    let reply = qmp
        .execute("query-status", Some(json!({ "alpha": 1 })))
        .await
        .unwrap();
    drop(qmp);

    assert_eq!(reply, json!({ "status": "running" }));
    assert_eq!(
        server.await.unwrap(),
        vec![
            json!({ "execute": "qmp_capabilities" }),
            json!({ "execute": "query-status", "arguments": { "alpha": 1 } }),
        ]
    );
}

#[tokio::test]
async fn an_error_reply_carries_its_desc() {
    let dir = TempDir::new().unwrap();
    let error = json!({ "error": { "class": "GenericError", "desc": "alpha is broken" } });
    let (path, _server) = serve(&dir, true, vec![vec![ok()], vec![error]]);

    let mut qmp = Qmp::connect(&path, TIMEOUT).await.unwrap();
    let err = qmp.execute("stop", None).await.unwrap_err();

    assert!(
        matches!(&err, AgentError::Qmp(message) if message == "stop: alpha is broken"),
        "{err}"
    );
}

#[tokio::test]
async fn events_are_skipped() {
    let dir = TempDir::new().unwrap();
    let event =
        |name: &str| json!({ "event": name, "timestamp": { "seconds": 1, "microseconds": 0 } });
    let (path, _server) = serve(
        &dir,
        true,
        vec![
            vec![event("RESUME"), ok()],
            vec![event("STOP"), json!({ "return": { "status": "paused" } })],
        ],
    );

    let mut qmp = Qmp::connect(&path, TIMEOUT).await.unwrap();
    let reply = qmp.execute("query-status", None).await.unwrap();

    assert_eq!(reply, json!({ "status": "paused" }));
}

#[tokio::test]
async fn a_server_that_never_greets_times_out() {
    let dir = TempDir::new().unwrap();
    let (path, _server) = serve(&dir, false, Vec::new());

    let Err(err) = Qmp::connect(&path, Duration::from_millis(200)).await else {
        panic!("connected without a greeting");
    };

    assert!(
        matches!(&err, AgentError::Qmp(message) if message == "timed out"),
        "{err}"
    );
}
