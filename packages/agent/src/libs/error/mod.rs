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
    #[allow(dead_code, reason = "driven by the reconcile loop, prompt 10")]
    #[error("vm: {0}")]
    Vm(String),
    #[allow(dead_code, reason = "driven by the reconcile loop, prompt 10")]
    #[error("qmp: {0}")]
    Qmp(String),
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Http(#[from] reqwest::Error),
}
