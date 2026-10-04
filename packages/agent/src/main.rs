mod libs;

use std::process::ExitCode;
use std::time::Duration;

use tokio::signal::unix::{signal, SignalKind};
use tracing_subscriber::EnvFilter;

use libs::agent::Agent;
use libs::api::HttpApi;
use libs::config::{self, Config};
use libs::error::AgentError;
use libs::runtime::QemuRuntime;

const RETRY: Duration = Duration::from_secs(5);

fn banner() -> String {
    format!("jatszoter-agent {}", env!("CARGO_PKG_VERSION"))
}

fn main() -> ExitCode {
    tracing_subscriber::fmt()
        .json()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .with_writer(std::io::stderr)
        .init();

    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            tracing::error!(error = %err, "jatszoter-agent stopped");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<(), AgentError> {
    let config = config::load()?;
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?
        .block_on(serve(config))
}

async fn serve(config: Config) -> Result<(), AgentError> {
    config.prepare()?;
    let api = HttpApi::new(config.api_url.clone())?;
    let runtime = QemuRuntime::new(&config, api.clone())?;
    let mut agent = Agent::new(&config, api, runtime)?;
    let mut terminate = signal(SignalKind::terminate())?;
    tracing::info!(name = %config.name, capacity = config.capacity, "{} started", banner());

    loop {
        let pause = agent.cycle().await.unwrap_or_else(|err| {
            tracing::warn!(error = %err, "agent cycle failed");
            RETRY
        });
        tokio::select! {
            _ = terminate.recv() => break,
            _ = tokio::signal::ctrl_c() => break,
            () = tokio::time::sleep(pause) => {}
        }
    }
    tracing::info!("jatszoter-agent stopped by signal");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::banner;

    #[test]
    fn banner_names_the_crate_and_version() {
        assert_eq!(
            banner(),
            format!("jatszoter-agent {}", env!("CARGO_PKG_VERSION"))
        );
    }
}
