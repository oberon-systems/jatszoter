use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

use crate::libs::api::{Desired, Status};
use crate::libs::vm::{valid_id, OnDisk};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Action {
    Start(Desired),
    Stop(Desired),
    Lost(Desired),
    Probe(Desired),
    Remove(PathBuf),
}

impl Action {
    /// The deployment the action works on; a VM directory is named after it.
    pub fn deployment_id(&self) -> String {
        match self {
            Self::Start(desired)
            | Self::Stop(desired)
            | Self::Lost(desired)
            | Self::Probe(desired) => desired.id.clone(),
            Self::Remove(dir) => dir_id(dir),
        }
    }
}

pub fn plan(
    desired: &[Desired],
    on_disk: &[OnDisk],
    busy: &HashSet<String>,
    capacity: u32,
) -> Vec<Action> {
    let disk: HashMap<String, &OnDisk> = on_disk.iter().map(|vm| (dir_id(&vm.dir), vm)).collect();
    let occupied = disk
        .iter()
        .filter(|(_, vm)| vm.alive)
        .map(|(id, _)| id)
        .chain(busy)
        .collect::<HashSet<_>>()
        .len();
    let mut free = usize::try_from(capacity)
        .unwrap_or(usize::MAX)
        .saturating_sub(occupied);
    let mut actions = Vec::new();
    for item in desired {
        if busy.contains(&item.id) || !valid_id(&item.id) {
            continue;
        }
        let vm = disk.get(&item.id).copied();
        match (item.status, vm) {
            (Status::Stopping, _) => actions.push(Action::Stop(item.clone())),
            (Status::Starting | Status::Bootstrapping | Status::Ready, _)
                if !vm.is_some_and(|vm| vm.alive) =>
            {
                actions.push(Action::Lost(item.clone()));
            }
            (Status::Starting | Status::Bootstrapping, _) => {
                actions.push(Action::Probe(item.clone()));
            }
            (
                Status::Pending | Status::Completed | Status::Failed | Status::Cancelled,
                Some(vm),
            ) if !vm.alive => actions.push(Action::Remove(vm.dir.clone())),
            (Status::Pending, None) if free > 0 => {
                free -= 1;
                actions.push(Action::Start(item.clone()));
            }
            _ => {}
        }
    }
    let wanted: HashSet<&str> = desired.iter().map(|item| item.id.as_str()).collect();
    for vm in on_disk {
        let id = dir_id(&vm.dir);
        if !wanted.contains(id.as_str()) && !busy.contains(&id) {
            actions.push(Action::Remove(vm.dir.clone()));
        }
    }
    actions
}

fn dir_id(dir: &Path) -> String {
    dir.file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests;
