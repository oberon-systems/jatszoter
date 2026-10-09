use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use tempfile::TempDir;
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::RwLock;
use tokio::task::JoinHandle;
use tokio::time::timeout;
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

/// A listener standing in for the API, and `ship` already dialling it.
async fn dialled(log: &Path) -> (TcpListener, Shipping) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = Url::parse(&format!("http://{}/", listener.local_addr().unwrap())).unwrap();
    let log = log.to_path_buf();
    let shipping = tokio::spawn(async move { ship(&url, &auth(), "gamma", &log).await });
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
    let (_dir, path) = log(b"login: ");
    let url = Url::parse("http://127.0.0.1:1/").unwrap();
    let auth: Auth = Arc::new(RwLock::new(None));

    let shipped = ship(&url, &auth, "gamma", &path).await;

    assert!(matches!(shipped, Err(AgentError::Api(_))));
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
