use std::path::PathBuf;
use std::time::Duration;

use super::{parse_api_url, parse_cidrs, parse_labels, parse_name, Config, RawConfig};

fn raw() -> RawConfig {
    RawConfig {
        api_url: "https://api.example.com".into(),
        name: "alpha".into(),
        ..RawConfig::default()
    }
}

#[test]
fn the_state_lives_in_the_user_cache_by_default() {
    let config = Config::parse(raw(), None, Some("/home/alpha".into())).unwrap();

    assert_eq!(
        config.state_dir,
        PathBuf::from("/home/alpha/.cache/jatszoter")
    );
    assert_eq!(config.capacity, 1);
    assert!(config.labels.is_empty());
}

#[test]
fn xdg_cache_home_wins_over_home() {
    let config = Config::parse(
        raw(),
        Some("/var/cache/beta".into()),
        Some("/home/alpha".into()),
    )
    .unwrap();

    assert_eq!(config.state_dir, PathBuf::from("/var/cache/beta/jatszoter"));
}

#[test]
fn an_explicit_state_dir_must_be_absolute() {
    let relative = RawConfig {
        state_dir: Some("state".into()),
        ..raw()
    };

    assert!(Config::parse(relative, None, Some("/home/alpha".into())).is_err());
}

#[test]
fn no_home_leaves_no_state_dir() {
    assert!(Config::parse(raw(), None, None).is_err());
}

#[test]
fn the_api_url_is_https_or_loopback() {
    assert_eq!(
        parse_api_url("https://api.example.com/v").unwrap().as_str(),
        "https://api.example.com/v/"
    );
    assert!(parse_api_url("http://127.0.0.1:8000").is_ok());
    assert!(parse_api_url("http://localhost:8000").is_ok());
    for bad in [
        "http://api.example.com",
        "https://user:pass@api.example.com",
        "https://api.example.com/?q=1",
        "ftp://api.example.com",
        "not a url",
    ] {
        assert!(parse_api_url(bad).is_err(), "{bad}");
    }
}

#[test]
fn names_and_labels_are_plain_words() {
    assert!(parse_name("alpha-1.beta_2").is_ok());
    assert!(parse_name("-alpha").is_err());
    assert!(parse_name("").is_err());
    assert_eq!(
        parse_labels(" gpu , kvm,,").unwrap(),
        vec!["gpu".to_owned(), "kvm".to_owned()]
    );
    assert!(parse_labels("a b").is_err());
    assert!(parse_labels(&["x"; 17].join(",")).is_err());
}

#[test]
fn capacity_is_bounded() {
    let too_many = RawConfig {
        capacity: Some(65),
        ..raw()
    };

    assert!(Config::parse(too_many, None, Some("/home/alpha".into())).is_err());
}

#[test]
fn the_image_download_is_strict_by_default() {
    let config = Config::parse(raw(), None, Some("/home/alpha".into())).unwrap();

    assert!(config.image_internal_cidrs.is_empty());
    assert!(!config.image_use_proxy);
}

#[test]
fn tcg_is_allowed_only_when_asked_for() {
    let home = || Some("/home/alpha".into());
    let asked = RawConfig {
        allow_tcg: Some(true),
        ..raw()
    };

    assert!(!Config::parse(raw(), None, home()).unwrap().allow_tcg);
    assert!(Config::parse(asked, None, home()).unwrap().allow_tcg);
}

#[test]
fn the_image_cache_holds_at_least_one_image() {
    let parse = |limit, cache| {
        Config::parse(
            RawConfig {
                image_limit_gib: limit,
                image_cache_gib: cache,
                ..raw()
            },
            None,
            Some("/home/alpha".into()),
        )
    };

    assert_eq!(parse(None, None).unwrap().image_cache_bytes, 100 << 30);
    assert_eq!(parse(Some(200), None).unwrap().image_cache_bytes, 200 << 30);
    assert_eq!(parse(None, Some(20)).unwrap().image_cache_bytes, 20 << 30);
    assert!(parse(None, Some(19)).is_err());
    assert!(parse(None, Some(16385)).is_err());
}

#[test]
fn the_image_download_deadline_is_bounded() {
    let parse = |minutes| {
        Config::parse(
            RawConfig {
                image_download_minutes: minutes,
                ..raw()
            },
            None,
            Some("/home/alpha".into()),
        )
    };

    assert_eq!(
        parse(None).unwrap().image_download_deadline,
        Duration::from_secs(3600)
    );
    assert_eq!(
        parse(Some(5)).unwrap().image_download_deadline,
        Duration::from_secs(300)
    );
    assert!(parse(Some(0)).is_err());
    assert!(parse(Some(1441)).is_err());
}

#[test]
fn internal_cidrs_are_a_list_of_networks() {
    let cidrs = parse_cidrs(" 192.0.2.0/24 , ::1/128,,").unwrap();

    assert_eq!(cidrs.len(), 2);
    assert_eq!(cidrs[0].to_string(), "192.0.2.0/24");
    assert!(parse_cidrs("").unwrap().is_empty());
    for bad in ["192.0.2.1", "192.0.2.0/33", "alpha"] {
        assert!(parse_cidrs(bad).is_err(), "{bad}");
    }
}

#[test]
fn the_firmware_defaults_to_the_ovmf_package() {
    let config = Config::parse(raw(), None, Some("/home/alpha".into())).unwrap();

    assert_eq!(
        config.ovmf_code,
        PathBuf::from("/usr/share/OVMF/OVMF_CODE_4M.fd")
    );
    assert_eq!(
        config.ovmf_vars,
        PathBuf::from("/usr/share/OVMF/OVMF_VARS_4M.fd")
    );
}

#[test]
fn the_firmware_paths_can_be_overridden() {
    let custom = RawConfig {
        ovmf_code: Some("/opt/edk2/code.fd".into()),
        ovmf_vars: Some("/opt/edk2/vars.fd".into()),
        ..raw()
    };

    let config = Config::parse(custom, None, Some("/home/alpha".into())).unwrap();

    assert_eq!(config.ovmf_code, PathBuf::from("/opt/edk2/code.fd"));
    assert_eq!(config.ovmf_vars, PathBuf::from("/opt/edk2/vars.fd"));
}
