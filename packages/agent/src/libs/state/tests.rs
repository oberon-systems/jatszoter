use std::fs;
use std::os::unix::fs::PermissionsExt;

use super::{Credential, State, StateStore};

fn state() -> State {
    State {
        agent_id: Some("alpha".into()),
        claim_code: None,
        credential: Some(Credential {
            credential: "current:1:1790000000".into(),
            expires_at: 1_790_000_000,
            key: "beta".into(),
        }),
    }
}

#[test]
fn a_missing_file_is_an_empty_state() {
    let dir = tempfile::tempdir().unwrap();

    assert_eq!(
        StateStore::new(dir.path()).load().unwrap(),
        State::default()
    );
}

#[test]
fn a_saved_state_reads_back_from_a_private_file() {
    let dir = tempfile::tempdir().unwrap();
    let store = StateStore::new(dir.path());

    store.save(&state()).unwrap();

    assert_eq!(store.load().unwrap(), state());
    let mode = fs::metadata(dir.path().join("state.json"))
        .unwrap()
        .permissions()
        .mode();
    assert_eq!(mode & 0o777, 0o600);
}

#[test]
fn a_state_file_others_can_read_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let store = StateStore::new(dir.path());
    store.save(&state()).unwrap();
    let path = dir.path().join("state.json");
    fs::set_permissions(&path, fs::Permissions::from_mode(0o644)).unwrap();

    assert!(store.load().is_err());
}

#[test]
fn secrets_never_reach_debug_output() {
    let mut state = state();
    state.claim_code = Some("gamma".into());

    let shown = format!("{state:?}");

    assert!(!shown.contains("beta"));
    assert!(!shown.contains("gamma"));
}
