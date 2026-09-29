use std::time::{Duration, Instant};

use super::LeaseClock;

#[test]
fn no_lease_never_expires() {
    assert!(!LeaseClock::default().expired(Instant::now()));
}

#[test]
fn the_deadline_counts_from_when_the_heartbeat_was_sent() {
    let sent_at = Instant::now();
    let mut clock = LeaseClock::default();

    clock.renewed(sent_at, Duration::from_secs(30));

    assert!(!clock.expired(sent_at + Duration::from_secs(29)));
    assert!(clock.expired(sent_at + Duration::from_secs(30)));
}

#[test]
fn a_cleared_clock_stops_expiring() {
    let sent_at = Instant::now();
    let mut clock = LeaseClock::default();
    clock.renewed(sent_at, Duration::ZERO);

    clock.clear();

    assert!(!clock.expired(sent_at + Duration::from_secs(60)));
}
