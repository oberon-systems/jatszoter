use hmac::Mac;
use serde_json::json;
use url::Url;
use wiremock::matchers::{body_json, method, path};
use wiremock::{Mock, MockServer, Request, ResponseTemplate};

use super::{mac, sign, Api, Claim, HttpApi, Lease, Signed};
use crate::libs::state::Credential;

fn credential() -> Credential {
    Credential {
        credential: "current:1:1790000000".into(),
        expires_at: 1_790_000_000,
        key: "beta".into(),
    }
}

fn api(server: &MockServer) -> HttpApi {
    HttpApi::new(Url::parse(&format!("{}/", server.uri())).unwrap()).unwrap()
}

fn signed_by(key: &'static str, credential: &'static str) -> impl Fn(&Request) -> bool {
    move |request: &Request| {
        let header = |name: &str| {
            request
                .headers
                .get(name)
                .and_then(|value| value.to_str().ok())
                .unwrap_or_default()
                .to_owned()
        };
        let timestamp = header("X-Timestamp");
        let expected = sign(key, &timestamp, "POST", request.url.path(), &request.body).unwrap();
        header("X-Credential") == credential && header("X-Signature") == expected
    }
}

#[tokio::test]
async fn register_sends_the_name_labels_and_known_id() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/v1/agents/register"))
        .and(body_json(
            json!({"name": "alpha", "labels": ["kvm"], "agent_id": "gamma"}),
        ))
        .respond_with(ResponseTemplate::new(201).set_body_json(json!({"claim_code": "delta"})))
        .expect(1)
        .mount(&server)
        .await;

    let code = api(&server)
        .register("alpha", &["kvm".to_owned()], Some("gamma"))
        .await
        .unwrap();

    assert_eq!(code, "delta");
}

#[tokio::test]
async fn a_signed_claim_grants_the_credential() {
    let server = MockServer::start().await;
    let body = json!({"agent_id": "gamma", "credential": {
        "credential": "current:1:1790000000", "expires_at": 1_790_000_000, "key": "beta"
    }})
    .to_string();
    let mut signer = mac(b"delta").unwrap();
    signer.update(body.as_bytes());
    let signature = hex::encode(signer.finalize().into_bytes());
    Mock::given(method("POST"))
        .and(path("/api/v1/agents/claim"))
        .and(body_json(json!({"code": "delta"})))
        .respond_with(
            ResponseTemplate::new(200)
                .insert_header("X-Signature", signature.as_str())
                .set_body_raw(body, "application/json"),
        )
        .mount(&server)
        .await;

    let claim = api(&server).claim("delta").await.unwrap();

    assert_eq!(
        claim,
        Claim::Granted {
            agent_id: "gamma".into(),
            credential: credential()
        }
    );
}

#[tokio::test]
async fn a_claim_signed_with_another_code_is_refused() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/v1/agents/claim"))
        .respond_with(
            ResponseTemplate::new(200)
                .insert_header("X-Signature", "00")
                .set_body_json(json!({"agent_id": "gamma", "credential": {
                    "credential": "current:1:1", "expires_at": 1, "key": "beta"
                }})),
        )
        .mount(&server)
        .await;

    assert!(api(&server).claim("delta").await.is_err());
}

#[tokio::test]
async fn a_pending_or_unknown_claim_is_reported() {
    for (status, expected) in [(409, Claim::Waiting), (401, Claim::Refused)] {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/api/v1/agents/claim"))
            .respond_with(ResponseTemplate::new(status))
            .mount(&server)
            .await;

        assert_eq!(api(&server).claim("delta").await.unwrap(), expected);
    }
}

#[tokio::test]
async fn a_heartbeat_is_signed_and_returns_the_lease() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/v1/agents/gamma/heartbeat"))
        .and(body_json(json!({"capacity": 2})))
        .and(signed_by("beta", "current:1:1790000000"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "id": "epsilon", "capacity": 2, "heartbeat_at": 100, "expires_at": 130
        })))
        .expect(1)
        .mount(&server)
        .await;

    let lease = api(&server)
        .heartbeat("gamma", &credential(), 2)
        .await
        .unwrap();

    assert_eq!(
        lease,
        Signed::Accepted(Lease {
            id: "epsilon".into(),
            capacity: 2,
            heartbeat_at: 100,
            expires_at: 130
        })
    );
}

#[tokio::test]
async fn a_refused_credential_is_reported() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .respond_with(ResponseTemplate::new(401))
        .mount(&server)
        .await;
    let api = api(&server);

    assert_eq!(
        api.heartbeat("gamma", &credential(), 1).await.unwrap(),
        Signed::Refused
    );
    assert_eq!(
        api.refresh("gamma", &credential()).await.unwrap(),
        Signed::Refused
    );
}

#[tokio::test]
async fn a_refresh_is_signed_over_an_empty_body() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/v1/agents/gamma/credential"))
        .and(signed_by("beta", "current:1:1790000000"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "credential": "current:2:1790003600", "expires_at": 1_790_003_600, "key": "zeta"
        })))
        .mount(&server)
        .await;

    let fresh = api(&server).refresh("gamma", &credential()).await.unwrap();

    assert_eq!(
        fresh,
        Signed::Accepted(Credential {
            credential: "current:2:1790003600".into(),
            expires_at: 1_790_003_600,
            key: "zeta".into(),
        })
    );
}

#[test]
fn the_signature_matches_the_api_format() {
    // Computed with Python's hmac the way the API's Credential.sign does it.
    let signature = sign(
        "beta",
        "1790000000",
        "POST",
        "/api/v1/agents/gamma/heartbeat",
        b"{}",
    )
    .unwrap();

    assert_eq!(
        signature,
        "f197ffba046b1ba165b8e9ca6feb06a8639ea1824762b44730b1b13a5bec717e"
    );
}
