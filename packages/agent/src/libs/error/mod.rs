#[derive(Debug, thiserror::Error)]
pub enum AgentError {
    #[error("config: {0}")]
    Config(String),
    #[error("state: {0}")]
    State(String),
    #[error("api: {0}")]
    Api(String),
    #[error("image: {0}")]
    Image(String),
    #[error("vm: {0}")]
    Vm(String),
    #[error("qmp: {0}")]
    Qmp(String),
    #[allow(dead_code, reason = "driven by the bootstrap, prompt 06")]
    #[error("qga: {0}")]
    Qga(String),
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Http(#[from] reqwest::Error),
}
