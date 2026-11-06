use std::fs;
use std::net::Ipv4Addr;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};

use tempfile::TempDir;

use super::{check_version, preflight, release_date, run, start, GATEWAY};
use crate::libs::config::NetConfig;
use crate::libs::vm::passt_args;

#[test]
fn release_dates_of_upstream_and_rpm_builds() {
    assert_eq!(release_date("passt 2026_10_02.cba3570"), Some(20_261_002));
    assert_eq!(
        release_date("passt 0^20250217.ga1e48a0-1.el9.x86_64"),
        Some(20_250_217)
    );
    assert_eq!(release_date("passt unknown version"), None);
    assert_eq!(release_date("passt 12345678"), None);
}

#[test]
fn an_old_or_unnamed_passt_is_refused() {
    assert!(check_version("passt 2026_10_02.cba3570").is_ok());
    assert!(check_version("passt 2027_01_15.0123abc").is_ok());
    assert!(check_version("passt 0^20240220.g1e6f92b-1.el9")
        .unwrap_err()
        .contains("older than 2026_10_02"));
    assert!(check_version("passt unknown version")
        .unwrap_err()
        .contains("names no release date"));
}

fn stub_passt(dir: &Path, version: &str) -> PathBuf {
    let path = dir.join("passt");
    fs::write(&path, format!("#!/bin/sh\necho 'passt {version}'\n")).unwrap();
    fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
    path
}

#[test]
fn serve_refuses_an_old_passt_and_root() {
    let tmp = TempDir::new().unwrap();
    let old = stub_passt(tmp.path(), "0^20240220.g1e6f92b-1.el9");
    let why = preflight(&old, 1000).unwrap_err().to_string();
    assert!(why.contains("older than 2026_10_02"), "{why}");

    let tmp = TempDir::new().unwrap();
    let fresh = stub_passt(tmp.path(), "2026_10_02.cba3570");
    assert!(preflight(&fresh, 1000).is_ok());
    let why = preflight(&fresh, 0).unwrap_err().to_string();
    assert!(why.contains("runs as root"), "{why}");
}

// Needs passt 2026_10_02 or newer on PATH and a host resolver: run with `-- --ignored`.
#[test]
#[ignore]
fn the_guest_network_passes_every_check() {
    let tmp = TempDir::new().unwrap();
    let config = NetConfig {
        state_dir: tmp.path().join("state"),
        passt: PathBuf::from("passt"),
    };

    let outcomes = run(&config);

    let failed: Vec<_> = outcomes.iter().filter(|o| o.result.is_err()).collect();
    assert!(failed.is_empty(), "{failed:#?}");
    assert_eq!(outcomes.len(), 5);
}

#[test]
#[ignore]
fn without_no_map_gw_the_gateway_reaches_the_host_loopback() {
    let tmp = TempDir::new().unwrap();
    let mapping = |dir: &std::path::Path| {
        passt_args(dir)
            .into_iter()
            .filter(|arg| arg != "--no-map-gw")
            .collect()
    };
    let mut passt = start("passt".as_ref(), tmp.path(), mapping).unwrap();

    assert!(passt.answers_arp());
    assert!(
        passt.walled_off(GATEWAY).is_err(),
        "the check must catch a gateway that leads to the host loopback"
    );
    assert!(passt.walled_off(Ipv4Addr::LOCALHOST).is_ok());
}
