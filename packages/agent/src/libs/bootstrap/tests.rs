use super::{overdue, verdict, Verdict};

#[test]
fn ok_is_ready() {
    assert_eq!(verdict(b"ok\n"), Verdict::Ready);
}

#[test]
fn a_failed_step_is_failed_with_the_marker_text() {
    assert_eq!(
        verdict(b"  failed configure 3\n"),
        Verdict::Failed("failed configure 3".into())
    );
}

#[test]
fn an_empty_marker_is_bad() {
    assert_eq!(verdict(b""), Verdict::Failed("bad marker".into()));
    assert_eq!(verdict(b" \n"), Verdict::Failed("bad marker".into()));
}

#[test]
fn a_garbled_marker_is_bad() {
    for marker in [
        &b"okay"[..],
        b"failed",
        b"failed configure",
        b"failed configure three",
        b"failed reboot 3",
        b"failed configure 3 again",
        b"failed con\x1b[2Jfigure 3",
        b"\xff\xfe",
    ] {
        assert_eq!(verdict(marker), Verdict::Failed("bad marker".into()));
    }
}

#[test]
fn control_characters_are_cleaned_from_the_reason() {
    assert_eq!(
        verdict(b"failed con\x07fig\xffure 3\x00"),
        Verdict::Failed("failed configure 3".into())
    );
}

#[test]
fn a_bootstrap_is_overdue_at_and_after_the_deadline() {
    assert!(!overdue(1_000, 300, 1_299));
    assert!(overdue(1_000, 300, 1_300));
    assert!(overdue(1_000, 300, 5_000));
}

#[test]
fn a_huge_deadline_does_not_overflow() {
    assert!(!overdue(u64::MAX, 300, u64::MAX - 1));
}
