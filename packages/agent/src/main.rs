mod libs;

use std::process::ExitCode;
use std::time::Duration;

use tokio::signal::unix::{signal, SignalKind};
use tracing_subscriber::EnvFilter;

use libs::agent::Agent;
use libs::api::HttpApi;
use libs::config::{self, Config};
use libs::error::AgentError;
use libs::netcheck;
use libs::runtime::QemuRuntime;

const RETRY: Duration = Duration::from_secs(5);

fn banner() -> String {
    format!("jatszoter-agent {}", env!("CARGO_PKG_VERSION"))
}

fn main() -> ExitCode {
    match std::env::args().nth(1).as_deref() {
        None => {}
        Some("check-network") => return check_network(),
        Some(other) => {
            eprintln!("unknown command {other:?}; usage: jatszoter-agent [check-network]");
            return ExitCode::from(2);
        }
    }
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

fn check_network() -> ExitCode {
    let config = match config::load_net() {
        Ok(config) => config,
        Err(err) => {
            eprintln!("{err}");
            return ExitCode::FAILURE;
        }
    };
    let outcomes = netcheck::run(&config);
    for outcome in &outcomes {
        match &outcome.result {
            Ok(()) => println!("ok    {}", outcome.name),
            Err(why) => println!("FAIL  {}: {why}", outcome.name),
        }
    }
    if outcomes.iter().all(|outcome| outcome.result.is_ok()) {
        return ExitCode::SUCCESS;
    }
    println!(
        "the guest network of {} is not safe to run VMs on; see {}",
        config.passt.display(),
        netcheck::RUNBOOK
    );
    ExitCode::FAILURE
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
