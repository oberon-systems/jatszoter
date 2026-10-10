use std::collections::VecDeque;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tempfile::TempDir;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::{TcpListener, TcpStream, UnixListener, UnixStream};
use tokio::sync::RwLock;
use tokio::task::JoinHandle;
use tokio::time::{sleep, timeout};
use tokio_tungstenite::tungstenite::handshake::server::{Request, Response};
use tokio_tungstenite::tungstenite::http;
use tokio_tungstenite::tungstenite::protocol::frame::coding::CloseCode;
use tokio_tungstenite::tungstenite::protocol::CloseFrame;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{accept_async, accept_hdr_async, WebSocketStream};
use url::Url;

use super::{read_at, ship, CHUNK};
use crate::libs::agent::{Auth, Signer};
use crate::libs::api::sign;
use crate::libs::error::AgentError;
use crate::libs::state::Credential;

const WAIT: Duration = Duration::from_secs(5);
const QUIET: Duration = Duration::from_millis(300);

type Socket = WebSocketStream<TcpStream>;
type Shipping = JoinHandle<Result<bool, AgentError>>;
type Runs = Arc<Mutex<Vec<Value>>>;

fn auth() -> Auth {
    Arc::new(RwLock::new(Some(Signer {
        agent_id: "alpha".into(),
        credential: Credential {
            credential: "current:1:1790000000".into(),
            expires_at: 1_790_000_000,
            key: "beta".into(),
        },
    })))
}

fn log(content: &[u8]) -> (TempDir, PathBuf) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("console.log");
    fs::write(&path, content).unwrap();
    (dir, path)
}

fn append(path: &Path, content: &[u8]) {
    OpenOptions::new()
        .append(true)
        .open(path)
        .unwrap()
        .write_all(content)
        .unwrap();
}

/// A listener standing in for the API, and `ship` already dialling it for the directory of `log`.
async fn dialled(log: &Path) -> (TcpListener, Shipping) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = Url::parse(&format!("http://{}/", listener.local_addr().unwrap())).unwrap();
    let dir = log.parent().unwrap().to_path_buf();
    let shipping = tokio::spawn(async move { ship(&url, &auth(), "gamma", &dir).await });
    (listener, shipping)
}

async fn accepted(listener: &TcpListener) -> Socket {
    let (stream, _) = listener.accept().await.unwrap();
    accept_async(stream).await.unwrap()
}

async fn answer(socket: &mut Socket, offset: u64) {
    socket
        .send(Message::Text(format!("{{\"offset\": {offset}}}")))
        .await
        .unwrap();
}

async fn frame(socket: &mut Socket) -> (u64, Vec<u8>) {
    match timeout(WAIT, socket.next())
        .await
        .unwrap()
        .unwrap()
        .unwrap()
    {
        Message::Binary(frame) => {
            let (offset, bytes) = frame.split_at(8);
            (
                u64::from_be_bytes(offset.try_into().unwrap()),
                bytes.to_vec(),
            )
        }
        other => panic!("not a binary frame: {}", other.len()),
    }
}

async fn close(socket: &mut Socket, code: u16) {
    socket
        .close(Some(CloseFrame {
            code: CloseCode::from(code),
            reason: "".into(),
        }))
        .await
        .unwrap();
}

fn serial(dir: &TempDir) -> UnixListener {
    UnixListener::bind(dir.path().join("serial.sock")).unwrap()
}

async fn connected(serial: &UnixListener) -> UnixStream {
    timeout(WAIT, serial.accept()).await.unwrap().unwrap().0
}

async fn typed(stream: &mut UnixStream, count: usize) -> Vec<u8> {
    let mut keys = vec![0; count];
    timeout(WAIT, stream.read_exact(&mut keys))
        .await
        .unwrap()
        .unwrap();
    keys
}

async fn keys(socket: &mut Socket, keys: &[u8]) {
    socket.send(Message::Binary(keys.to_vec())).await.unwrap();
}

async fn tell(socket: &mut Socket, text: &str) {
    socket.send(Message::Text(text.into())).await.unwrap();
}

async fn resize(socket: &mut Socket, cols: u64, rows: u64) {
    tell(socket, &json!({ "cols": cols, "rows": rows }).to_string()).await;
}

/// A guest agent on `qga.sock`: records every `guest-exec`; the n-th exits with `codes[n]`, then 0.
fn guest_agent(dir: &TempDir, codes: &[i64]) -> Runs {
    let listener = UnixListener::bind(dir.path().join("qga.sock")).unwrap();
    let runs = Runs::default();
    let seen = Arc::clone(&runs);
    let mut codes: VecDeque<i64> = codes.iter().copied().collect();
    tokio::spawn(async move {
        loop {
            let (stream, _) = listener.accept().await.unwrap();
            let (reader, mut writer) = stream.into_split();
            let mut reader = BufReader::new(reader);
            let mut line = Vec::new();
            let mut code = 0;
            while reader.read_until(b'\n', &mut line).await.unwrap_or(0) > 0 {
                let synced = line.first() == Some(&0xFF);
                let sent: Value = serde_json::from_slice(&line[usize::from(synced)..]).unwrap();
                let answer = match sent["execute"].as_str().unwrap() {
                    "guest-sync-delimited" => sent["arguments"]["id"].clone(),
                    "guest-exec" => {
                        code = codes.pop_front().unwrap_or(0);
                        seen.lock().unwrap().push(sent["arguments"].clone());
                        json!({ "pid": 7 })
                    }
                    _ => json!({ "exited": true, "exitcode": code }),
                };
                let mut reply = if synced { vec![0xFF] } else { Vec::new() };
                reply.extend(format!("{}\n", json!({ "return": answer })).bytes());
                if writer.write_all(&reply).await.is_err() {
                    break;
                }
                line.clear();
            }
        }
    });
    runs
}

/// Waits until the guest agent was asked to run `count` commands.
async fn ran(runs: &Runs, count: usize) -> Vec<Value> {
    let waiting = async {
        loop {
            let seen = runs.lock().unwrap().clone();
            if seen.len() >= count {
                return seen;
            }
            sleep(Duration::from_millis(10)).await;
        }
    };
    timeout(WAIT, waiting).await.unwrap()
}

fn stty(cols: u64, rows: u64) -> Value {
    json!({
        "path": "stty",
        "arg": ["-F", "/dev/ttyS0", "cols", cols.to_string(), "rows", rows.to_string()],
        "capture-output": false
    })
}

#[tokio::test]
#[allow(
    clippy::result_large_err,
    reason = "the library fixes the callback's error type"
)]
async fn the_upgrade_carries_the_three_headers_and_the_signature_verifies() {
    let (_dir, path) = log(b"");
    let (listener, shipping) = dialled(&path).await;
    let (stream, _) = listener.accept().await.unwrap();
    let mut seen = Vec::new();
    let mut socket = accept_hdr_async(stream, |request: &Request, response: Response| {
        seen.push(request.uri().path().to_owned());
        for name in ["X-Credential", "X-Timestamp", "X-Signature"] {
            let value = request.headers().get(name).unwrap().to_str().unwrap();
            seen.push(value.to_owned());
        }
        Ok(response)
    })
    .await
    .unwrap();
    close(&mut socket, 1000).await;

    assert!(!shipping.await.unwrap().unwrap());
    let [path, credential, timestamp, signature] = seen.try_into().unwrap();
    assert_eq!(path, "/api/v1/agents/alpha/deployments/gamma/console");
    assert_eq!(credential, "current:1:1790000000");
    assert_eq!(
        signature,
        sign("beta", &timestamp, "GET", &path, &[]).unwrap()
    );
}

#[tokio::test]
async fn the_first_frame_starts_at_the_offset_the_server_answers() {
    let (_dir, path) = log(b"login: alpha\r\n");
    let (listener, shipping) = dialled(&path).await;
    let mut socket = accepted(&listener).await;

    answer(&mut socket, 7).await;

    assert_eq!(frame(&mut socket).await, (7, b"alpha\r\n".to_vec()));
    shipping.abort();
}

#[tokio::test]
async fn an_answer_below_the_sent_offset_is_resent_from_it() {
    let (_dir, path) = log(b"login: alpha\r\n");
    let (listener, shipping) = dialled(&path).await;
    let mut socket = accepted(&listener).await;
    answer(&mut socket, 7).await;
    frame(&mut socket).await;

    answer(&mut socket, 3).await;

    assert_eq!(frame(&mut socket).await, (3, b"in: alpha\r\n".to_vec()));
    shipping.abort();
}

#[tokio::test]
async fn bytes_appended_while_connected_arrive_in_a_later_frame() {
    let (_dir, path) = log(b"login: ");
    let (listener, shipping) = dialled(&path).await;
    let mut socket = accepted(&listener).await;
    answer(&mut socket, 0).await;
    assert_eq!(frame(&mut socket).await, (0, b"login: ".to_vec()));
    answer(&mut socket, 7).await;

    append(&path, b"alpha\r\n");

    assert_eq!(frame(&mut socket).await, (7, b"alpha\r\n".to_vec()));
    shipping.abort();
}

#[tokio::test]
async fn a_frame_carries_at_most_64_kib() {
    let content: Vec<u8> = (0..CHUNK + 10).map(|at| (at % 251) as u8).collect();
    let (_dir, path) = log(&content);
    let (listener, shipping) = dialled(&path).await;
    let mut socket = accepted(&listener).await;

    answer(&mut socket, 0).await;
    assert_eq!(frame(&mut socket).await, (0, content[..CHUNK].to_vec()));
    answer(&mut socket, CHUNK as u64).await;

    assert_eq!(
        frame(&mut socket).await,
        (CHUNK as u64, content[CHUNK..].to_vec())
    );
    shipping.abort();
}

#[tokio::test]
async fn no_frame_is_sent_while_one_is_unanswered() {
    let (_dir, path) = log(b"login: ");
    let (listener, shipping) = dialled(&path).await;
    let mut socket = accepted(&listener).await;
    assert!(timeout(QUIET, socket.next()).await.is_err());
    answer(&mut socket, 0).await;
    frame(&mut socket).await;

    append(&path, b"alpha\r\n");

    assert!(timeout(QUIET, socket.next()).await.is_err());
    answer(&mut socket, 7).await;
    assert_eq!(frame(&mut socket).await, (7, b"alpha\r\n".to_vec()));
    shipping.abort();
}

#[tokio::test]
async fn a_4413_close_returns_full() {
    let (_dir, path) = log(b"login: ");
    let (listener, shipping) = dialled(&path).await;
    let mut socket = accepted(&listener).await;
    answer(&mut socket, 0).await;
    frame(&mut socket).await;

    close(&mut socket, 4413).await;

    assert!(shipping.await.unwrap().unwrap());
}

#[tokio::test]
#[allow(
    clippy::result_large_err,
    reason = "the library fixes the callback's error type"
)]
async fn a_refused_upgrade_is_an_error() {
    let (_dir, path) = log(b"login: ");
    let (listener, shipping) = dialled(&path).await;
    let (stream, _) = listener.accept().await.unwrap();

    let refused = accept_hdr_async(stream, |_: &Request, _: Response| {
        Err(http::Response::builder().status(403).body(None).unwrap())
    })
    .await;

    assert!(refused.is_err());
    assert!(matches!(
        shipping.await.unwrap(),
        Err(AgentError::Api(message)) if message.contains("403")
    ));
}

#[tokio::test]
async fn an_agent_without_a_credential_does_not_connect() {
    let (dir, _path) = log(b"login: ");
    let url = Url::parse("http://127.0.0.1:1/").unwrap();
    let auth: Auth = Arc::new(RwLock::new(None));

    let shipped = ship(&url, &auth, "gamma", dir.path()).await;

    assert!(matches!(shipped, Err(AgentError::Api(_))));
}

#[tokio::test]
async fn keys_from_two_frames_arrive_in_order_on_one_connection() {
    let (dir, path) = log(b"");
    let serial = serial(&dir);
    let (listener, shipping) = dialled(&path).await;
    let mut socket = accepted(&listener).await;

    keys(&mut socket, b"alpha").await;
    keys(&mut socket, b"beta").await;

    let mut stream = connected(&serial).await;
    assert_eq!(typed(&mut stream, 9).await, b"alphabeta");
    assert!(timeout(QUIET, serial.accept()).await.is_err());
    shipping.abort();
}

#[tokio::test]
async fn what_the_serial_socket_writes_back_is_drained_while_frames_keep_shipping() {
    let (dir, path) = log(b"login: ");
    let serial = serial(&dir);
    let (listener, shipping) = dialled(&path).await;
    let mut socket = accepted(&listener).await;
    keys(&mut socket, b"alpha").await;
    let mut stream = connected(&serial).await;
    typed(&mut stream, 5).await;

    let echo = vec![b'a'; 1 << 20];
    timeout(WAIT, stream.write_all(&echo))
        .await
        .unwrap()
        .unwrap();
    answer(&mut socket, 0).await;

    assert_eq!(frame(&mut socket).await, (0, b"login: ".to_vec()));
    shipping.abort();
}

#[tokio::test]
async fn a_connection_that_closed_is_opened_again_by_the_next_key() {
    let (dir, path) = log(b"");
    let serial = serial(&dir);
    let (listener, shipping) = dialled(&path).await;
    let mut socket = accepted(&listener).await;
    keys(&mut socket, b"alpha").await;
    let mut stream = connected(&serial).await;
    typed(&mut stream, 5).await;
    drop(stream);

    // The write that meets the closed connection loses its keys.
    keys(&mut socket, b"beta").await;
    keys(&mut socket, b"gamma").await;

    let mut stream = connected(&serial).await;
    assert_eq!(typed(&mut stream, 5).await, b"gamma");
    shipping.abort();
}

#[tokio::test]
async fn without_a_serial_socket_the_keys_are_lost_and_the_log_still_ships() {
    let (_dir, path) = log(b"login: ");
    let (listener, shipping) = dialled(&path).await;
    let mut socket = accepted(&listener).await;

    keys(&mut socket, b"alpha").await;
    answer(&mut socket, 0).await;

    assert_eq!(frame(&mut socket).await, (0, b"login: ".to_vec()));
    assert!(!shipping.is_finished());
    shipping.abort();
}

#[tokio::test]
async fn a_resize_runs_stty_with_the_size_as_separate_arguments() {
    let (dir, path) = log(b"login: ");
    let runs = guest_agent(&dir, &[]);
    let (listener, shipping) = dialled(&path).await;
    let mut socket = accepted(&listener).await;

    resize(&mut socket, 120, 40).await;
    answer(&mut socket, 0).await;

    assert_eq!(ran(&runs, 1).await, vec![stty(120, 40)]);
    assert_eq!(frame(&mut socket).await, (0, b"login: ".to_vec()));
    shipping.abort();
}

#[tokio::test]
async fn the_same_size_twice_runs_stty_once() {
    let (dir, path) = log(b"");
    let runs = guest_agent(&dir, &[]);
    let (listener, shipping) = dialled(&path).await;
    let mut socket = accepted(&listener).await;

    resize(&mut socket, 80, 24).await;
    resize(&mut socket, 80, 24).await;
    resize(&mut socket, 100, 30).await;

    assert_eq!(ran(&runs, 2).await, vec![stty(80, 24), stty(100, 30)]);
    shipping.abort();
}

#[tokio::test]
async fn a_failed_stty_is_run_again_by_the_next_frame() {
    let (dir, path) = log(b"");
    let runs = guest_agent(&dir, &[1]);
    let (listener, shipping) = dialled(&path).await;
    let mut socket = accepted(&listener).await;

    resize(&mut socket, 80, 24).await;
    resize(&mut socket, 80, 24).await;
    resize(&mut socket, 80, 24).await;
    resize(&mut socket, 100, 30).await;

    assert_eq!(
        ran(&runs, 3).await,
        vec![stty(80, 24), stty(80, 24), stty(100, 30)]
    );
    shipping.abort();
}

#[tokio::test]
async fn a_resize_without_a_guest_agent_is_tried_again_and_the_log_still_ships() {
    let (dir, path) = log(b"login: ");
    let (listener, shipping) = dialled(&path).await;
    let mut socket = accepted(&listener).await;
    resize(&mut socket, 80, 24).await;
    answer(&mut socket, 0).await;
    assert_eq!(frame(&mut socket).await, (0, b"login: ".to_vec()));

    let runs = guest_agent(&dir, &[]);
    resize(&mut socket, 80, 24).await;

    assert_eq!(ran(&runs, 1).await, vec![stty(80, 24)]);
    shipping.abort();
}

#[tokio::test]
async fn cols_0_and_rows_501_run_nothing() {
    let (dir, path) = log(b"");
    let runs = guest_agent(&dir, &[]);
    let (listener, shipping) = dialled(&path).await;
    let mut socket = accepted(&listener).await;

    resize(&mut socket, 0, 24).await;
    resize(&mut socket, 80, 501).await;
    tell(&mut socket, r#"{"cols": 80}"#).await;
    tell(&mut socket, r#"{"cols": "80", "rows": "24"}"#).await;
    resize(&mut socket, 100, 30).await;

    assert_eq!(ran(&runs, 1).await, vec![stty(100, 30)]);
    shipping.abort();
}

#[test]
fn read_at_reads_from_the_offset() {
    let (_dir, path) = log(b"login: alpha");
    let mut buffer = [0; 5];

    assert_eq!(read_at(&path, 7, &mut buffer).unwrap(), 5);
    assert_eq!(&buffer, b"alpha");
}

#[test]
fn read_at_past_the_end_and_on_a_missing_file_reads_nothing() {
    let (dir, path) = log(b"login: ");
    let mut buffer = [0; 16];

    assert_eq!(read_at(&path, 7, &mut buffer).unwrap(), 0);
    assert_eq!(read_at(&path, 100, &mut buffer).unwrap(), 0);
    assert_eq!(
        read_at(&dir.path().join("missing.log"), 0, &mut buffer).unwrap(),
        0
    );
}
