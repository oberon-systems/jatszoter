use std::collections::HashSet;
use std::path::PathBuf;
use std::slice;

use super::{plan, Action};
use crate::libs::api::{Desired, ImageRef, Resources, ScenarioRef, Status, Timeouts};
use crate::libs::vm::{OnDisk, Record};

fn desired(id: &str, status: Status) -> Desired {
    Desired {
        id: id.into(),
        vm_id: format!("vm-{id}"),
        status,
        scenario: ScenarioRef {
            id: "alpha".into(),
            slug: "alpha".into(),
            version: 1,
        },
        image: ImageRef {
            name: "beta".into(),
            url: "https://example.com/beta.qcow2".into(),
            digest: "0".repeat(64),
        },
        resources: Resources {
            cpus: 1,
            memory_mib: 512,
            disk_gib: 4,
        },
        timeouts: Timeouts {
            bootstrap_seconds: 300,
            check_seconds: 30,
            session_minutes: 60,
        },
    }
}

fn dir(id: &str) -> PathBuf {
    PathBuf::from("/state/vms").join(id)
}

fn vm(id: &str, alive: bool) -> OnDisk {
    OnDisk {
        dir: dir(id),
        record: Some(Record {
            deployment_id: id.into(),
            vm_id: format!("vm-{id}"),
            created_at: 0,
        }),
        alive,
    }
}

fn orphan(id: &str) -> OnDisk {
    OnDisk {
        dir: dir(id),
        record: None,
        alive: false,
    }
}

fn idle() -> HashSet<String> {
    HashSet::new()
}

#[test]
fn an_assigned_pending_deployment_starts() {
    let item = desired("alpha", Status::Pending);

    let actions = plan(slice::from_ref(&item), &[], &idle(), 2);

    assert_eq!(actions, vec![Action::Start(item)]);
}

#[test]
fn a_stopping_deployment_stops() {
    let item = desired("alpha", Status::Stopping);

    let actions = plan(slice::from_ref(&item), &[vm("alpha", true)], &idle(), 2);

    assert_eq!(actions, vec![Action::Stop(item)]);
}

#[test]
fn a_live_vm_of_a_ready_deployment_is_adopted() {
    let actions = plan(
        &[desired("alpha", Status::Ready)],
        &[vm("alpha", true)],
        &idle(),
        2,
    );

    assert!(actions.is_empty());
}

#[test]
fn a_full_agent_starts_nothing_new() {
    let actions = plan(
        &[
            desired("alpha", Status::Ready),
            desired("beta", Status::Pending),
        ],
        &[vm("alpha", true)],
        &idle(),
        1,
    );

    assert!(actions.is_empty());
}

#[test]
fn a_start_in_flight_takes_a_slot() {
    let busy = HashSet::from(["alpha".to_owned()]);

    let actions = plan(
        &[
            desired("alpha", Status::Starting),
            desired("beta", Status::Pending),
        ],
        &[],
        &busy,
        1,
    );

    assert!(actions.is_empty());
}

#[test]
fn busy_deployments_get_no_action() {
    let busy = HashSet::from(["alpha".to_owned(), "beta".to_owned(), "gamma".to_owned()]);

    let actions = plan(
        &[
            desired("alpha", Status::Pending),
            desired("beta", Status::Stopping),
            desired("gamma", Status::Starting),
        ],
        &[vm("delta", false)],
        &busy,
        8,
    );

    assert_eq!(actions, vec![Action::Remove(dir("delta"))]);
}

#[test]
fn a_vm_of_an_unknown_deployment_is_removed() {
    let actions = plan(&[], &[vm("alpha", true), vm("beta", false)], &idle(), 2);

    assert_eq!(
        actions,
        vec![Action::Remove(dir("alpha")), Action::Remove(dir("beta"))]
    );
}

#[test]
fn a_starting_deployment_with_a_dead_vm_is_lost() {
    let item = desired("alpha", Status::Starting);

    let actions = plan(slice::from_ref(&item), &[vm("alpha", false)], &idle(), 2);

    assert_eq!(actions, vec![Action::Lost(item)]);
}

#[test]
fn an_orphan_directory_is_removed() {
    let actions = plan(&[], &[orphan("alpha")], &idle(), 2);

    assert_eq!(actions, vec![Action::Remove(dir("alpha"))]);
}

#[test]
fn a_dead_vm_of_a_pending_deployment_is_removed_before_it_starts() {
    let actions = plan(
        &[desired("alpha", Status::Pending)],
        &[vm("alpha", false)],
        &idle(),
        2,
    );

    assert_eq!(actions, vec![Action::Remove(dir("alpha"))]);
}

#[test]
fn a_deployment_id_that_is_not_a_plain_word_is_ignored() {
    let actions = plan(&[desired("../alpha", Status::Stopping)], &[], &idle(), 2);

    assert!(actions.is_empty());
}

#[test]
fn a_live_starting_vm_is_probed() {
    let item = desired("alpha", Status::Starting);

    let actions = plan(slice::from_ref(&item), &[vm("alpha", true)], &idle(), 2);

    assert_eq!(actions, vec![Action::Probe(item)]);
}

#[test]
fn a_live_bootstrapping_vm_is_probed() {
    let item = desired("alpha", Status::Bootstrapping);

    let actions = plan(slice::from_ref(&item), &[vm("alpha", true)], &idle(), 2);

    assert_eq!(actions, vec![Action::Probe(item)]);
}

#[test]
fn a_busy_starting_vm_is_not_probed() {
    let busy = HashSet::from(["alpha".to_owned()]);

    let actions = plan(
        &[desired("alpha", Status::Starting)],
        &[vm("alpha", true)],
        &busy,
        2,
    );

    assert!(actions.is_empty());
}

#[test]
fn a_probe_names_its_deployment() {
    assert_eq!(
        Action::Probe(desired("alpha", Status::Starting)).deployment_id(),
        "alpha"
    );
}
