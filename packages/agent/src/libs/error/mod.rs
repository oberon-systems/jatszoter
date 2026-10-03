#[derive(Debug, thiserror::Error)]
pub enum AgentError {
    #[error("config: {0}")]
    Config(String),
    #[error("state: {0}")]
    State(String),
    #[error("api: {0}")]
    Api(String),
    #[allow(dead_code, reason = "driven by the reconcile loop, prompt 10")]
    #[error("image: {0}")]
    Image(String),
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Http(#[from] reqwest::Error),
}
