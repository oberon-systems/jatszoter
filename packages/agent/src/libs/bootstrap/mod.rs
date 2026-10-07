pub const MARKER: &str = "/var/lib/jatszoter/marker";

const MAX_REASON: usize = 200;
const BAD: &str = "bad marker";
const STEPS: [&str; 3] = ["install", "configure", "break"];

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    Ready,
    Failed(String),
}

/// The reason keeps printable ASCII only: it reaches the operator.
pub fn verdict(marker: &[u8]) -> Verdict {
    let text: String = marker
        .trim_ascii()
        .iter()
        .filter(|byte| (b' '..=b'~').contains(*byte))
        .map(|&byte| char::from(byte))
        .collect();
    if text == "ok" {
        return Verdict::Ready;
    }
    let words: Vec<&str> = text.split_ascii_whitespace().collect();
    match words[..] {
        ["failed", step, code]
            if STEPS.contains(&step) && code.bytes().all(|byte| byte.is_ascii_digit()) =>
        {
            Verdict::Failed(text.chars().take(MAX_REASON).collect())
        }
        _ => Verdict::Failed(BAD.into()),
    }
}

pub fn overdue(created_at: u64, bootstrap_seconds: u64, now: u64) -> bool {
    now >= created_at.saturating_add(bootstrap_seconds)
}

#[cfg(test)]
mod tests;
