pub mod agent;
pub mod api;
pub mod config;
pub mod error;
pub mod images;
pub mod lease;
#[allow(dead_code, reason = "driven by the reconcile loop, prompt 10")]
pub mod qmp;
pub mod state;
#[allow(dead_code, reason = "driven by the reconcile loop, prompt 10")]
pub mod vm;
