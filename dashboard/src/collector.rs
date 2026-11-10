//! In-memory time series for the dashboard.
//!
//! The engine writes one `engine_state` file per second and never keeps history,
//! so a monitor that only reads files can only ever show *now*. This keeps a small
//! ring of samples in memory — no file, no database, nothing persisted. A cold
//! start therefore reports an honest `since_ts`; the chart never invents history
//! it did not observe.
//!
//! Three resolutions: 2 h at 1 s, 48 h at 30 s, 14 d at 5 min. Each is bucketed on
//! write and downsampled with LTTB on read, so a 14-day series still costs a few
//! thousand bytes on the wire.

use serde_json::{json, Value};
use std::collections::VecDeque;

const MAX_POINTS: usize = 1000;
const EVENT_CAP: usize = 500;

#[derive(Debug, Clone, Copy, Default)]
struct Sample {
    ts: i64,
    equity: f64,
    balance: f64,
}

#[derive(Debug, Default)]
pub struct Collector {
    since_ts: i64,
    last_raw_ts: i64,
    s1: VecDeque<Sample>,
    s30: VecDeque<Sample>,
    s300: VecDeque<Sample>,
    events: VecDeque<Value>,
}

impl Collector {
    pub fn new() -> Self {
        Self::default()
    }

    /// Record one observation. Called once per second by the snapshot watcher with
    /// whatever the engine last wrote. Buckets keep the last write in each slot,
    /// which is what a 30-second or 5-minute view means.
    pub fn sample(&mut self, ts: i64, equity: Option<f64>, balance: Option<f64>) {
        let (Some(equity), Some(balance)) = (equity, balance) else {
            return; // no engine state: a gap, not a zero
        };
        if ts <= self.last_raw_ts {
            return;
        }
        self.last_raw_ts = ts;
        if self.since_ts == 0 {
            self.since_ts = ts;
        }
        let s = Sample {
            ts,
            equity,
            balance,
        };
        push_bucketed(&mut self.s1, s, 1, 2 * 3600);
        push_bucketed(&mut self.s30, s, 30, 48 * 3600);
        push_bucketed(&mut self.s300, s, 300, 14 * 86400);
    }

    /// Append an event to the rail. Deduplicated on (kind, label, ts) so a 1 Hz
    /// rebuild of the same ledger row does not fill the list.
    pub fn event(&mut self, ts: i64, kind: &str, label: &str, scan_id: Option<&str>) {
        let dup = self.events.iter().rev().take(4).any(|e| {
            e["ts"] == json!(ts) && e["kind"] == json!(kind) && e["label"] == json!(label)
        });
        if dup {
            return;
        }
        self.events.push_back(json!({
            "ts": ts, "kind": kind, "label": label, "scan_id": scan_id
        }));
        while self.events.len() > EVENT_CAP {
            self.events.pop_front();
        }
    }

    pub fn snapshot(&self) -> Value {
        json!({
            "since_ts": if self.since_ts == 0 { Value::Null } else { json!(self.since_ts) },
            "equity": {
                "2h": points(&self.s1),
                "48h": points(&self.s30),
                "14d": points(&self.s300),
            },
            "balance": {
                "2h": points_balance(&self.s1),
                "48h": points_balance(&self.s30),
                "14d": points_balance(&self.s300),
            },
            "events": self.events.iter().cloned().collect::<Vec<_>>(),
        })
    }
}

/// One spelling of a duration, matching the browser's `fmtDur`: 45s, 2m, 1h 30m,
/// 1d 1h. Server-side so an alert message can carry it without the client
/// reformatting prose.
pub fn human_age(secs: i64) -> String {
    let n = secs.max(0);
    if n < 60 {
        return format!("{n}s");
    }
    let m = n / 60;
    if n < 3600 {
        return if n % 60 == 0 { format!("{m}m") } else { format!("{m}m {}s", n % 60) };
    }
    let h = m / 60;
    if h < 24 {
        return if m % 60 == 0 { format!("{h}h") } else { format!("{h}h {}m", m % 60) };
    }
    let d = h / 24;
    if h % 24 == 0 { format!("{d}d") } else { format!("{d}d {}h", h % 24) }
}

/// Keep the last sample per bucket and drop anything older than `keep_s`.
fn push_bucketed(buf: &mut VecDeque<Sample>, s: Sample, bucket: i64, keep_s: i64) {
    let slot = s.ts / bucket;
    match buf.back() {
        Some(last) if last.ts / bucket == slot => {
            if let Some(last) = buf.back_mut() {
                *last = s;
            }
        }
        _ => buf.push_back(s),
    }
    let cutoff = s.ts - keep_s;
    while buf.front().is_some_and(|p| p.ts < cutoff) {
        buf.pop_front();
    }
}

fn points(buf: &VecDeque<Sample>) -> Value {
    let pairs: Vec<(i64, f64)> = buf.iter().map(|s| (s.ts, s.equity)).collect();
    json!(lttb(&pairs, MAX_POINTS))
}

fn points_balance(buf: &VecDeque<Sample>) -> Value {
    let pairs: Vec<(i64, f64)> = buf.iter().map(|s| (s.ts, s.balance)).collect();
    json!(lttb(&pairs, MAX_POINTS))
}

/// Largest-Triangle-Three-Buckets, the standard shape-preserving downsample: it
/// keeps the extremes a stride would erase, which is exactly what matters in an
/// equity curve headed for zero.
pub fn lttb(data: &[(i64, f64)], threshold: usize) -> Vec<[f64; 2]> {
    let n = data.len();
    if threshold >= n || threshold < 3 {
        return data.iter().map(|(x, y)| [*x as f64, *y]).collect();
    }
    let mut out: Vec<[f64; 2]> = Vec::with_capacity(threshold);
    out.push([data[0].0 as f64, data[0].1]);
    let every = (n - 2) as f64 / (threshold - 2) as f64;
    let mut a = 0usize;
    for i in 0..threshold - 2 {
        // Next bucket's average, used as the third triangle vertex.
        let avg_start = ((i + 1) as f64 * every) as usize + 1;
        let avg_end = (((i + 2) as f64 * every) as usize + 1).min(n);
        let (mut avg_x, mut avg_y, mut count) = (0.0, 0.0, 0usize);
        for item in data.iter().take(avg_end).skip(avg_start) {
            avg_x += item.0 as f64;
            avg_y += item.1;
            count += 1;
        }
        if count == 0 {
            continue;
        }
        avg_x /= count as f64;
        avg_y /= count as f64;

        let range_start = (i as f64 * every) as usize + 1;
        let range_end = (((i + 1) as f64 * every) as usize + 1).min(n);
        let (ax, ay) = (data[a].0 as f64, data[a].1);
        let mut best = range_start;
        let mut best_area = -1.0;
        for (idx, item) in data.iter().enumerate().take(range_end).skip(range_start) {
            let area = ((ax - avg_x) * (item.1 - ay) - (ax - item.0 as f64) * (avg_y - ay)).abs();
            if area > best_area {
                best_area = area;
                best = idx;
            }
        }
        out.push([data[best].0 as f64, data[best].1]);
        a = best;
    }
    out.push([data[n - 1].0 as f64, data[n - 1].1]);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_cold_start_says_since_null_and_keeps_no_points() {
        let c = Collector::new();
        let s = c.snapshot();
        assert!(s["since_ts"].is_null());
        assert_eq!(s["equity"]["2h"].as_array().unwrap().len(), 0);
    }

    #[test]
    fn a_missing_engine_sample_is_a_gap_not_a_zero() {
        let mut c = Collector::new();
        c.sample(100, None, Some(10.0));
        c.sample(100, Some(1.0), None);
        assert_eq!(c.snapshot()["equity"]["2h"].as_array().unwrap().len(), 0);
        assert!(c.snapshot()["since_ts"].is_null());
    }

    #[test]
    fn the_1s_series_keeps_every_second() {
        let mut c = Collector::new();
        c.sample(1000, Some(1.0), Some(1.0));
        c.sample(1001, Some(2.0), Some(2.0));
        c.sample(1060, Some(3.0), Some(3.0));
        let e = c.snapshot()["equity"]["2h"].as_array().unwrap().clone();
        assert_eq!(e.len(), 3);
        assert_eq!(e[2][1], 3.0);
    }

    #[test]
    fn a_coarse_bucket_keeps_the_last_write_in_its_slot() {
        // 30s slots: 990..1019 is slot 33 (so 1000 and 1019 collapse), 1050 starts 35.
        let mut c = Collector::new();
        c.sample(1000, Some(1.0), Some(1.0));
        c.sample(1019, Some(7.0), Some(7.0));
        c.sample(1060, Some(3.0), Some(3.0));
        let e = c.snapshot()["equity"]["48h"].as_array().unwrap().clone();
        assert_eq!(e.len(), 2);
        assert_eq!(e[0][1], 7.0, "the later write in the same 30s slot wins");
    }

    #[test]
    fn the_30s_series_buckets_and_the_1s_series_does_not() {
        let mut c = Collector::new();
        for i in 0..90 {
            c.sample(1000 + i, Some(i as f64), Some(0.0));
        }
        let s = c.snapshot();
        assert_eq!(s["equity"]["2h"].as_array().unwrap().len(), 90);
        // ts 1000..1089 covers 30s slots 33, 34, 35 and 36.
        assert_eq!(s["equity"]["48h"].as_array().unwrap().len(), 4);
    }

    #[test]
    fn old_samples_fall_out_of_their_window() {
        let mut c = Collector::new();
        c.sample(0, Some(1.0), Some(1.0));
        c.sample(2 * 3600 + 10, Some(2.0), Some(2.0));
        let e = c.snapshot()["equity"]["2h"].as_array().unwrap().len();
        assert_eq!(e, 1);
    }

    #[test]
    fn a_duplicate_event_is_ignored() {
        let mut c = Collector::new();
        c.event(5, "verdict", "STAND_DOWN", Some("a"));
        c.event(5, "verdict", "STAND_DOWN", Some("a"));
        assert_eq!(c.snapshot()["events"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn lttb_keeps_the_endpoints_and_the_threshold() {
        let pts: Vec<(i64, f64)> = (0..5000).map(|i| (i as i64, (i % 97) as f64)).collect();
        let out = lttb(&pts, 1000);
        assert_eq!(out.len(), 1000);
        assert_eq!(out[0], [0.0, 0.0]);
        assert_eq!(out[999], [4999.0, 4999.0 % 97.0]);
        // Strictly increasing time: a downsample must never reorder the series.
        assert!(out.windows(2).all(|w| w[0][0] < w[1][0]));
    }

    #[test]
    fn lttb_passes_a_short_series_through() {
        let pts = vec![(1i64, 1.0), (2, 2.0)];
        assert_eq!(lttb(&pts, 1000).len(), 2);
    }
}
