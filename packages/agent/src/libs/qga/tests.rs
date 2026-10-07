use std::path::PathBuf;
use std::time::Duration;

use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use serde_json::{json, Value};
use tempfile::TempDir;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixListener;
use tokio::task::JoinHandle;

use super::{Qga, CHUNK};
use crate::libs::error::AgentError;

const TIMEOUT: Duration = Duration::from_secs(5);
const MARKER: &str = "/alpha/marker";
const HANDLE: i64 = 1000;

/// Accepts one client, answers its sync after `stale`, then the n-th command with the n-th reply.
fn serve(
    dir: &TempDir,
    stale: &'static [u8],
    replies: Vec<Vec<u8>>,
) -> (PathBuf, JoinHandle<Vec<Value>>) {
    let path = dir.path().join("qga.sock");
    let listener = UnixListener::bind(&path).unwrap();
    let task = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let (reader, mut writer) = stream.into_split();
        let mut reader = BufReader::new(reader);
        let mut line = Vec::new();
        reader.read_until(b'\n', &mut line).await.unwrap();
        let sync: Value = serde_json::from_slice(line.strip_prefix(&[0xFF]).unwrap()).unwrap();
        assert_eq!(sync["execute"], "guest-sync-delimited");
        let mut answer = stale.to_vec();
        answer.push(0xFF);
        answer.extend(reply(json!({ "return": sync["arguments"]["id"] })));
        writer.write_all(&answer).await.unwrap();
        let mut seen = Vec::new();
        for next in replies {
            line.clear();
            if reader.read_until(b'\n', &mut line).await.unwrap() == 0 {
                break;
            }
            seen.push(serde_json::from_slice(&line).unwrap());
            writer.write_all(&next).await.unwrap();
        }
        line.clear();
        while reader.read_until(b'\n', &mut line).await.unwrap_or(0) > 0 {
            line.clear();
        }
        seen
    });
    (path, task)
}

fn reply(value: Value) -> Vec<u8> {
    format!("{value}\n").into_bytes()
}

fn ok() -> Vec<u8> {
    reply(json!({ "return": {} }))
}

fn opened() -> Vec<u8> {
    reply(json!({ "return": HANDLE }))
}

fn chunk(data: &[u8], eof: bool) -> Vec<u8> {
    reply(json!({
        "return": { "count": data.len(), "buf-b64": STANDARD.encode(data), "eof": eof }
    }))
}

fn error(desc: &str) -> Vec<u8> {
    reply(json!({ "error": { "class": "GenericError", "desc": desc } }))
}

fn open() -> Value {
    json!({ "execute": "guest-file-open", "arguments": { "path": MARKER, "mode": "r" } })
}

fn read() -> Value {
    json!({ "execute": "guest-file-read", "arguments": { "handle": HANDLE, "count": CHUNK } })
}

fn close() -> Value {
    json!({ "execute": "guest-file-close", "arguments": { "handle": HANDLE } })
}

#[tokio::test]
async fn sync_drops_a_stale_reply_and_a_stray_delimiter() {
    let dir = TempDir::new().unwrap();
    let stale = b"{\"return\": {\"version\": \"stale\"}}\n\xff\xff{\"return\": 1}\n";
    let (path, server) = serve(
        &dir,
        stale,
        vec![reply(json!({ "return": { "version": "alpha" } }))],
    );

    let mut qga = Qga::connect(&path, TIMEOUT).await.unwrap();
    let info = qga.execute("guest-info", None).await.unwrap();
    drop(qga);

    assert_eq!(info, json!({ "version": "alpha" }));
    assert_eq!(
        server.await.unwrap(),
        vec![json!({ "execute": "guest-info" })]
    );
}

#[tokio::test]
async fn ping_answers() {
    let dir = TempDir::new().unwrap();
    let (path, server) = serve(&dir, b"", vec![ok()]);

    let mut qga = Qga::connect(&path, TIMEOUT).await.unwrap();
    qga.ping().await.unwrap();
    drop(qga);

    assert_eq!(
        server.await.unwrap(),
        vec![json!({ "execute": "guest-ping" })]
    );
}

#[tokio::test]
async fn a_file_reads_in_two_chunks() {
    let dir = TempDir::new().unwrap();
    let (path, server) = serve(
        &dir,
        b"",
        vec![
            opened(),
            chunk(b"failed ", false),
            chunk(b"configure 2\n", true),
            ok(),
        ],
    );

    let mut qga = Qga::connect(&path, TIMEOUT).await.unwrap();
    let data = qga.read_file(MARKER, 64).await.unwrap();
    drop(qga);

    assert_eq!(data.as_deref(), Some(&b"failed configure 2\n"[..]));
    assert_eq!(server.await.unwrap(), vec![open(), read(), read(), close()]);
}

#[tokio::test]
async fn a_missing_file_is_none() {
    let dir = TempDir::new().unwrap();
    let desc = "failed to open file '/alpha/marker' (mode: 'r'): No such file or directory";
    let (path, server) = serve(&dir, b"", vec![error(desc)]);

    let mut qga = Qga::connect(&path, TIMEOUT).await.unwrap();
    let data = qga.read_file(MARKER, 64).await.unwrap();
    drop(qga);

    assert_eq!(data, None);
    assert_eq!(server.await.unwrap(), vec![open()]);
}

#[tokio::test]
async fn a_guest_agent_that_never_answers_times_out() {
    let dir = TempDir::new().unwrap();
    let path = dir.path().join("qga.sock");
    let _listener = UnixListener::bind(&path).unwrap();

    let Err(err) = Qga::connect(&path, Duration::from_millis(200)).await else {
        panic!("synced with a silent guest agent");
    };

    assert!(
        matches!(&err, AgentError::Qga(message) if message == "guest-sync-delimited: timed out"),
        "{err}"
    );
}

#[tokio::test]
async fn an_error_reply_carries_its_desc() {
    let dir = TempDir::new().unwrap();
    let (path, _server) = serve(&dir, b"", vec![error("alpha is broken")]);

    let mut qga = Qga::connect(&path, TIMEOUT).await.unwrap();
    let err = qga.ping().await.unwrap_err();

    assert!(
        matches!(&err, AgentError::Qga(message) if message == "guest-ping: alpha is broken"),
        "{err}"
    );
}

#[tokio::test]
async fn a_file_over_max_is_an_error_and_still_closed() {
    let dir = TempDir::new().unwrap();
    let (path, server) = serve(
        &dir,
        b"",
        vec![opened(), chunk(b"alpha beta\n", false), ok()],
    );

    let mut qga = Qga::connect(&path, TIMEOUT).await.unwrap();
    let err = qga.read_file(MARKER, 4).await.unwrap_err();
    drop(qga);

    assert!(
        matches!(&err, AgentError::Qga(message) if message == "/alpha/marker: over 4 bytes"),
        "{err}"
    );
    assert_eq!(server.await.unwrap(), vec![open(), read(), close()]);
}

#[tokio::test]
async fn a_reply_that_is_not_json_is_an_error() {
    let dir = TempDir::new().unwrap();
    let (path, _server) = serve(&dir, b"", vec![b"alpha\n".to_vec()]);

    let mut qga = Qga::connect(&path, TIMEOUT).await.unwrap();
    let err = qga.ping().await.unwrap_err();

    assert!(
        matches!(&err, AgentError::Qga(message) if message.starts_with("guest-ping: bad message")),
        "{err}"
    );
}
