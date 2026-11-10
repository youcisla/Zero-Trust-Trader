//! Bounded parser for `funnel_history.log`.
//!
//! The ledger is append-only and grows forever, so this reads a fixed tail window
//! and never the whole file (AGENTS.md §9: the tail is bounded). Each line is one
//! scan: `ts | key=value ... | VERDICT sig=...`.
//!
//! The dashboard reports only the *presence* of a `sig=` segment. It holds no key,
//! so it cannot verify the chain, and claiming a verified row would be a lie. The
//! verification belongs to `status.py` / `trials_total.py`.

use serde_json::{json, Map, Value};
use std::collections::VecDeque;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

/// 512 KiB of tail is roughly 2,000 scans — more than the 400 rows returned.
pub const WINDOW_BYTES: u64 = 512 * 1024;
/// Rows kept in a snapshot. The page shows the newest first and never needs more.
pub const MAX_ROWS: usize = 400;

/// The parsed shape of one ledger line.
#[derive(Debug, Clone, PartialEq)]
pub struct Row {
    pub ts: String,
    pub universe: Option<i64>,
    pub tested: Option<i64>,
    pub strategies: Option<i64>,
    pub total_tests: Option<i64>,
    pub triggering: Option<i64>,
    pub deduped: Option<i64>,
    pub shortlist: Option<i64>,
    pub passed_both: Option<i64>,
    pub trials: Option<i64>,
    pub cumulative: Option<i64>,
    pub scan_id: Option<String>,
    pub permutations: Option<i64>,
    /// One of `STAND_DOWN`, `WINNER`, `FAILED`, `REBUILT`, or `OTHER`.
    pub verdict: String,
    pub winners: Vec<String>,
    /// That a `sig=` segment was present. Not a verification.
    pub signed: bool,
    /// Free text after a FAILED/REBUILT verdict (e.g. "preflight").
    pub detail: Option<String>,
}

impl Row {
    fn to_json(&self) -> Value {
        json!({
            "ts": self.ts,
            "universe": self.universe,
            "tested": self.tested,
            "strategies": self.strategies,
            "total_tests": self.total_tests,
            "triggering": self.triggering,
            "deduped": self.deduped,
            "shortlist": self.shortlist,
            "passed_both": self.passed_both,
            "trials": self.trials,
            "cumulative": self.cumulative,
            "scan_id": self.scan_id,
            "permutations": self.permutations,
            "verdict": self.verdict,
            "winners": self.winners,
            "signed": self.signed,
            "detail": self.detail,
        })
    }
}

/// Parse one ledger line. Returns `None` for anything that is not a ledger row —
/// a blank line, a comment, or a half-written tail — so a malformed line can
/// never become a fabricated scan.
pub fn parse_line(line: &str) -> Option<Row> {
    let line = line.trim_end_matches(['\r', '\n']).trim();
    if line.is_empty() {
        return None;
    }
    let mut parts = line.split('|').map(str::trim);
    let ts = parts.next()?;
    if ts.len() < 10 || !ts.contains('T') {
        return None;
    }
    let body = parts.next()?;
    let tail = parts.next().unwrap_or("");

    let mut fields: Map<String, Value> = Map::new();
    for token in body.split_whitespace() {
        if let Some((k, v)) = token.split_once('=') {
            if let Ok(n) = v.parse::<i64>() {
                fields.insert(k.to_string(), json!(n));
            } else {
                fields.insert(k.to_string(), json!(v));
            }
        }
    }

    let num = |k: &str| fields.get(k).and_then(Value::as_i64);
    let text = |k: &str| {
        fields
            .get(k)
            .and_then(Value::as_str)
            .map(|s| s.to_string())
    };

    // The verdict segment: a word, optional ":" payload, optional " sig=..." suffix.
    let signed = tail.split_whitespace().any(|t| t.starts_with("sig="));
    let head: Vec<&str> = tail
        .split_whitespace()
        .filter(|t| !t.starts_with("sig="))
        .collect();
    let head = head.join(" ");
    let (word, rest) = match head.split_once(':') {
        Some((w, r)) => (w.to_string(), Some(r.trim().to_string())),
        None => (
            head.split_whitespace().next().unwrap_or("").to_string(),
            None,
        ),
    };
    let verdict = match word.as_str() {
        "STAND_DOWN" => "STAND_DOWN",
        "WINNER" => "WINNER",
        "FAILED" => "FAILED",
        "REBUILT" => "REBUILT",
        _ => "OTHER",
    }
    .to_string();

    let mut winners = Vec::new();
    let mut detail = None;
    match verdict.as_str() {
        "WINNER" => {
            if let Some(r) = rest.as_deref() {
                winners = r
                    .split(',')
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .map(|s| s.to_string())
                    .collect();
            }
        }
        "FAILED" | "REBUILT" => {
            detail = rest.or_else(|| {
                let h = head.split_once(' ').map(|x| x.1).unwrap_or("").trim();
                if h.is_empty() {
                    None
                } else {
                    Some(h.to_string())
                }
            });
        }
        _ => {}
    }

    Some(Row {
        ts: ts.to_string(),
        universe: num("universe"),
        tested: num("tested"),
        strategies: num("strategies"),
        total_tests: num("total_tests"),
        triggering: num("triggering"),
        deduped: num("deduped"),
        shortlist: num("shortlist"),
        passed_both: num("passed_both"),
        trials: num("trials"),
        cumulative: num("cumulative"),
        scan_id: text("scan_id"),
        permutations: num("permutations"),
        verdict,
        winners,
        signed,
        detail,
    })
}

/// Read at most `WINDOW_BYTES` from the end of the file.
fn read_tail(path: &Path, window: u64) -> Option<(String, u64, bool)> {
    let mut f = File::open(path).ok()?;
    let total = f.metadata().ok()?.len();
    let start = total.saturating_sub(window);
    f.seek(SeekFrom::Start(start)).ok()?;
    let mut buf = Vec::new();
    f.take(window).read_to_end(&mut buf).ok()?;
    let text = String::from_utf8_lossy(&buf).into_owned();
    // When the window starts mid-line the first fragment is not a row; drop it
    // unless the window covers the whole file.
    let text = if start == 0 {
        text
    } else {
        match text.find('\n') {
            Some(i) => text[i + 1..].to_string(),
            None => String::new(),
        }
    };
    Some((text, total, start > 0))
}

/// The newest-first rows plus the accounting of what the window covered.
pub fn snapshot(path: &Path) -> Value {
    let (text, total, truncated) = match read_tail(path, WINDOW_BYTES) {
        Some(v) => v,
        None => {
            return json!({
                "rows": [],
                "window_bytes": WINDOW_BYTES,
                "truncated": false,
                "total_bytes": 0,
                "malformed": 0,
                "present": false
            })
        }
    };
    let mut rows: VecDeque<Row> = VecDeque::with_capacity(MAX_ROWS);
    let mut malformed = 0usize;
    for line in text.lines() {
        match parse_line(line) {
            Some(r) => {
                rows.push_front(r);
                if rows.len() > MAX_ROWS {
                    rows.pop_back();
                }
            }
            None => malformed += 1,
        }
    }
    json!({
        "rows": rows.iter().map(Row::to_json).collect::<Vec<_>>(),
        "window_bytes": WINDOW_BYTES,
        "truncated": truncated,
        "total_bytes": total,
        "malformed": malformed,
        "present": true
    })
}

#[cfg(test)]
mod tests {
    use super::*;

/// A unique directory under the OS temp dir. Deliberately not `tempfile`: adding a
/// dev-dependency would need a registry round-trip, and `cargo test --locked
/// --offline` has to keep working on a machine with no network.
fn tmpdir(tag: &str) -> std::path::PathBuf {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let p = std::env::temp_dir().join(format!("ztt-test-{}-{}-{}", tag, std::process::id(), nanos));
    std::fs::create_dir_all(&p).expect("temp dir");
    p
}


    const STAND_DOWN: &str = "2026-11-10T10:29:41Z | universe=120 tested=119 strategies=2 total_tests=238 triggering=15 deduped=1 shortlist=4 passed_both=0 trials=238 cumulative=11080 scan_id=3844a179ea804ddc98fceebca6b4cb0d permutations=1000 | STAND_DOWN sig=f9459ce5323cb7fa";

    #[test]
    fn a_stand_down_row_carries_its_accounting() {
        let r = parse_line(STAND_DOWN).expect("row");
        assert_eq!(r.verdict, "STAND_DOWN");
        assert_eq!(r.universe, Some(120));
        assert_eq!(r.total_tests, Some(238));
        assert_eq!(r.cumulative, Some(11080));
        assert_eq!(r.scan_id.as_deref(), Some("3844a179ea804ddc98fceebca6b4cb0d"));
        assert!(r.signed);
        assert!(r.winners.is_empty());
    }

    #[test]
    fn a_winner_row_names_its_survivors() {
        let line = "2026-11-01T05:00:00Z | universe=50 tested=47 strategies=1 total_tests=47 triggering=2 deduped=0 shortlist=2 passed_both=1 trials=47 cumulative=2252 scan_id=abc permutations=1000 | WINNER:ETHAUD/rsi,NZDUSD/rsi sig=deadbeef";
        let r = parse_line(line).expect("row");
        assert_eq!(r.verdict, "WINNER");
        assert_eq!(r.winners, vec!["ETHAUD/rsi", "NZDUSD/rsi"]);
        assert_eq!(r.passed_both, Some(1));
    }

    #[test]
    fn an_unsigned_row_is_reported_as_unsigned_not_as_verified() {
        let line = "2026-11-01T06:00:00Z | universe=50 tested=47 total_tests=47 | STAND_DOWN";
        let r = parse_line(line).expect("row");
        assert!(!r.signed);
        assert_eq!(r.verdict, "STAND_DOWN");
    }

    #[test]
    fn a_failed_row_keeps_the_stage_that_failed() {
        let line = "2026-11-09T21:28:53Z | universe=0 tested=0 total_tests=0 | FAILED preflight sig=aa";
        let r = parse_line(line).expect("row");
        assert_eq!(r.verdict, "FAILED");
        assert_eq!(r.detail.as_deref(), Some("preflight"));
        assert_eq!(r.total_tests, Some(0));
    }

    #[test]
    fn a_malformed_line_is_skipped_not_invented() {
        assert!(parse_line("").is_none());
        assert!(parse_line("not a ledger line at all").is_none());
        assert!(parse_line("| | |").is_none());
    }

    #[test]
    fn the_window_drops_the_half_line_it_started_in() {
        let dir = tmpdir("ledger");
        let path = dir.join("funnel_history.log");
        let mut body = String::new();
        for i in 0..3 {
            body.push_str(&format!(
                "2026-11-10T10:0{i}:00Z | universe=1 tested=1 total_tests=3 | STAND_DOWN sig=aa\n"
            ));
        }
        std::fs::write(&path, body).unwrap();
        let snap = snapshot(&path);
        assert_eq!(snap["rows"].as_array().unwrap().len(), 3);
        assert_eq!(snap["rows"][0]["ts"], "2026-11-10T10:02:00Z");
        assert_eq!(snap["truncated"], false);
    }

    #[test]
    fn newest_rows_come_first() {
        let dir = tmpdir("ledger");
        let path = dir.join("funnel_history.log");
        std::fs::write(
            &path,
            "2026-11-10T10:00:00Z | total_tests=3 | STAND_DOWN\n2026-11-10T10:01:00Z | total_tests=3 | STAND_DOWN\n",
        )
        .unwrap();
        let snap = snapshot(&path);
        assert_eq!(snap["rows"][0]["ts"], "2026-11-10T10:01:00Z");
        assert_eq!(snap["rows"][1]["ts"], "2026-11-10T10:00:00Z");
    }
}
