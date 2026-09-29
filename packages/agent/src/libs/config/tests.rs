use std::path::PathBuf;

use super::{parse_api_url, parse_labels, parse_name, Config, RawConfig};

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
