use std::time::{Duration, Instant};

/// The agent's own view of its lease; once the deadline passes it fences itself.
#[derive(Debug, Default)]
pub struct LeaseClock {
    deadline: Option<Instant>,
}

impl LeaseClock {
    // Measured from when the heartbeat was sent, so the local deadline never outlives the API's.
    pub fn renewed(&mut self, sent_at: Instant, ttl: Duration) {
        self.deadline = Some(sent_at + ttl);
    }

    pub fn expired(&self, now: Instant) -> bool {
        self.deadline.is_some_and(|deadline| now >= deadline)
    }

    pub fn clear(&mut self) {
        self.deadline = None;
    }
}

#[cfg(test)]
mod tests;
