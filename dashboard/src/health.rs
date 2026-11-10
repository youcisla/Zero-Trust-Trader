//! File freshness, the maintenance marker, and the alert rail.
//!
//! Every alert is derived here and nowhere else: the client renders what the
//! server says. A missing artifact produces `null`/absence, never a zero, and an
//! alert always carries the timestamp it started being true.

use serde_json::{json, Map, Value};
use std::path::Path;
use std::time::UNIX_EPOCH;

/// The artifacts the UI reads. A name that is absent from `files` is a file the
/// dashboard does not look at, not a file that is healthy.
pub const TRACKED: &[&str] = &[
    "engine_state.json",
    "tier2_candidates.json",
    "tier3_results.json",
    "scan_progress.json",
    "agent_notes.json",
    "desk_history.json",
    "liquid_universe.json",
    "funnel_history.log",
    "position_manager.log",
];

fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn age_of(path: &Path) -> Option<i64> {
    let m = std::fs::metadata(path).ok()?;
    if !m.is_file() {
        return None;
    }
    let modified = m.modified().ok()?.duration_since(UNIX_EPOCH).ok()?.as_secs() as i64;
    // A file with a future mtime (clock skew, a copy) reports 0 rather than a
    // negative age that would render as "-3s old".
    Some((now() - modified).max(0))
}

/// `{ name: {exists, age_s} }`. `engine_state.json` is matched by prefix because
/// the engine writes generation-suffixed names; the caller passes the resolved
/// path separately for that one.
pub fn files(root: &Path) -> Value {
    let mut map = Map::new();
    for name in TRACKED {
        let p = root.join(name);
        let exists = p.is_file();
        map.insert(
            name.to_string(),
            if exists {
                json!({"exists": true, "age_s": age_of(&p)})
            } else {
                json!({"exists": false, "age_s": null})
            },
        );
    }
    Value::Object(map)
}

/// `.ztt-maintenance` is fail-closed: while it exists every scheduled scan and
/// supervisor pass refuses. It is read here so the UI can say so; the dashboard
/// never creates or deletes it.
pub fn maintenance(root: &Path) -> Value {
    let p = root.join(".ztt-maintenance");
    let Some(age_s) = age_of(&p) else {
        return json!({"active": false, "age_min": null, "note": null});
    };
    let note = std::fs::read_to_string(&p)
        .ok()
        .and_then(|t| {
            t.lines()
                .map(str::trim)
                .find(|l| !l.is_empty())
                .map(|l| l.chars().take(200).collect::<String>())
        });
    json!({
        "active": true,
        "age_min": age_s / 60,
        "note": note
    })
}

/// Every alert the UI should raise, derived from facts already in the snapshot.
///
/// Eight facts in, one list out. Bundling them in a struct would rename the same
/// eight call-site values without removing one.
#[allow(clippy::too_many_arguments)]
pub fn alerts(
    engine_present: bool,
    engine_age_s: Option<i64>,
    engine_ok: bool,
    engine_error: Option<&str>,
    latest_row: Option<&Value>,
    agent: Option<&Value>,
    maintenance: &Value,
    ledger_present: bool,
) -> Value {
    let ts = now();
    let mut out: Vec<Value> = Vec::new();
    let mut push = |id: &str, severity: &str, message: String| {
        out.push(json!({"id": id, "severity": severity, "message": message, "since_ts": ts}));
    };

    if !engine_present {
        push(
            "engine_missing",
            "error",
            "no engine state — positions unknown".into(),
        );
    } else if !engine_ok {
        push(
            "engine_refusing",
            "error",
            match engine_error {
                Some(e) if !e.is_empty() => format!("engine refusing — {e}"),
                _ => "engine refusing — protection unverified".into(),
            },
        );
    } else if engine_age_s.is_some_and(|a| a > 120) {
        push(
            "engine_stale",
            "warn",
            format!(
                "engine state {} stale",
                crate::collector::human_age(engine_age_s.unwrap_or(0))
            ),
        );
    }

    if let Some(row) = latest_row {
        let verdict = row["verdict"].as_str().unwrap_or("");
        if verdict == "FAILED" {
            let detail = row["detail"].as_str().unwrap_or("unknown stage");
            let at = row["ts"].as_str().unwrap_or("").to_string();
            push(
                "scan_failed",
                "warn",
                format!("scan FAILED {detail} at {at}"),
            );
        }
        if !row["signed"].as_bool().unwrap_or(true) && verdict != "FAILED" {
            push(
                "ledger_unsigned",
                "info",
                "ledger rows without a sig= segment — chain not verified here".into(),
            );
        }
    } else if !ledger_present {
        push(
            "no_ledger",
            "info",
            "no scan has run on this tree yet".into(),
        );
    }

    if let Some(a) = agent {
        let status = a["status"].as_str().unwrap_or("");
        if status == "no key" {
            push(
                "desk_no_key",
                "warn",
                "no model key — the desk called nothing".into(),
            );
        } else if status == "rejected" {
            push(
                "desk_rejected",
                "warn",
                "the desk's note failed the citation guard".into(),
            );
        } else if status == "error" {
            // The roles carry the reason ("HTTP 401 from the model endpoint").
            // Without this the page showed four dashes and the audit said
            // nothing: an auth outage that ran for three hours unanswered.
            let why = a["roles"]
                .as_object()
                .and_then(|r| {
                    r.values()
                        .find_map(|v| v.get("reason").and_then(|x| x.as_str()))
                })
                .unwrap_or("no reason recorded");
            push(
                "desk_error",
                "bad",
                format!("the desk's model call failed — {why}"),
            );
        }
    }

    if maintenance["active"].as_bool().unwrap_or(false) {
        let note = maintenance["note"].as_str().unwrap_or("no note left");
        push(
            "maintenance_active",
            "warn",
            format!("maintenance active — every scheduled run refuses ({note})"),
        );
    }

    json!(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

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

    struct Dir(std::path::PathBuf);
    impl Dir {
        fn path(&self) -> &std::path::Path {
            &self.0
        }
    }
    fn tmp() -> Dir {
        Dir(tmpdir("health"))
    }

    #[test]
    fn an_absent_file_is_absent_not_zero_age() {
        let d = tmp();
        let f = files(d.path());
        assert_eq!(f["engine_state.json"]["exists"], false);
        assert!(f["engine_state.json"]["age_s"].is_null());
    }

    #[test]
    fn a_present_file_reports_an_age() {
        let d = tmp();
        std::fs::write(d.path().join("liquid_universe.json"), "{}").unwrap();
        let f = files(d.path());
        assert_eq!(f["liquid_universe.json"]["exists"], true);
        assert!(f["liquid_universe.json"]["age_s"].as_i64().unwrap() < 60);
    }

    #[test]
    fn a_failed_desk_call_raises_an_alert_that_says_why() {
        // Three hours of HTTP 401 were invisible: the page showed four dashes and
        // the alert rail was silent. The reason the roles carry is the alert.
        let agent = serde_json::json!({
            "status": "error",
            "roles": {
                "scout": {"status": "error", "reason": "HTTP 401 from the model endpoint"},
                "chief": {"status": "error", "reason": "HTTP 401 from the model endpoint"}
            }
        });
        let a = alerts(false, None, true, None, None, Some(&agent),
                       &serde_json::json!({"active": false}), false);
        let hit = a.as_array().unwrap().iter()
            .find(|x| x["id"] == "desk_error")
            .expect("the failure must surface");
        assert_eq!(hit["severity"], "bad");
        assert!(hit["message"].as_str().unwrap().contains("401"));
    }

    #[test]
    fn no_marker_means_no_maintenance() {
        let d = tmp();
        assert_eq!(maintenance(d.path())["active"], false);
    }

    #[test]
    fn the_marker_names_its_age_and_its_note() {
        let d = tmp();
        let mut f = std::fs::File::create(d.path().join(".ztt-maintenance")).unwrap();
        writeln!(f, "stopping the scheduler for the audit").unwrap();
        let m = maintenance(d.path());
        assert_eq!(m["active"], true);
        assert_eq!(m["note"], "stopping the scheduler for the audit");
        assert!(m["age_min"].is_number());
    }

    #[test]
    fn no_engine_raises_a_missing_alert_not_a_stale_one() {
        let a = alerts(false, None, true, None, None, None, &json!({"active": false}), false);
        let ids: Vec<&str> = a
            .as_array()
            .unwrap()
            .iter()
            .map(|x| x["id"].as_str().unwrap())
            .collect();
        assert!(ids.contains(&"engine_missing"));
        assert!(!ids.contains(&"engine_stale"));
        assert!(ids.contains(&"no_ledger"));
    }

    #[test]
    fn a_failing_engine_and_a_failed_scan_both_surface() {
        let row = json!({"verdict": "FAILED", "detail": "preflight", "ts": "2026-11-10T09:00:00Z", "signed": true});
        let a = alerts(true, Some(5), false, Some("mt5 init failed"), Some(&row), None, &json!({"active": false}), true);
        let text = a.to_string();
        assert!(text.contains("engine_refusing"));
        assert!(text.contains("mt5 init failed"));
        assert!(text.contains("scan_failed"));
        assert!(text.contains("preflight"));
    }

    #[test]
    fn an_unsigned_row_is_reported_as_unverified() {
        let row = json!({"verdict": "STAND_DOWN", "signed": false, "ts": "2026-11-10T09:00:00Z"});
        let a = alerts(true, Some(1), true, None, Some(&row), None, &json!({"active": false}), true);
        assert!(a.to_string().contains("ledger_unsigned"));
    }

    #[test]
    fn maintenance_surfaces_with_its_own_note() {
        let a = alerts(
            true,
            Some(1),
            true,
            None,
            None,
            None,
            &json!({"active": true, "note": "audit window", "age_min": 5}),
            true,
        );
        let text = a.to_string();
        assert!(text.contains("maintenance_active"));
        assert!(text.contains("audit window"));
    }
}
