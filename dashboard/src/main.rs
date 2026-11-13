//! Local read-only dashboard for the Zero-Trust Trader engine.
//!
//! Design rule: **the Python engine must never know this process exists.**
//! There is no IPC, no socket the engine connects to, no callback. The engine
//! writes ordinary files as part of its normal work; this process tails them.
//! Kill the dashboard and the trading system does not notice. Kill the engine
//! and the dashboard shows a stale-data age instead of lying.
//!
//! Endpoints
//!   GET /              the UI (single embedded HTML file, React vendored locally)
//!   GET /api/snapshot  current state, one shot
//!   GET /ws            the snapshot on change (checked at 1 Hz), resent at least
//!                      every 5 s, pinged every 15 s; age advances in the browser
//!   GET /architecture  the generated stage map
//!   POST /api/scan     the only write: starts run_funnel.py, no argument
//!   GET /vendor/*      vendored React and ECharts, hash-pinned
//!   GET /api/logs/*    bounded tails of the files the engine writes
//!   GET /api/report/latest  the producer's HTML report, if one exists

use axum::{
    extract::{
        ws::{Message, WebSocket, WebSocketUpgrade},
        Path as AxumPath, Request, State,
    },
    http::{header, HeaderMap, StatusCode},
    middleware::{self, Next},
    response::{Html, IntoResponse, Json, Response},
    routing::{get, post},
    Router,
};
use serde_json::{json, Value};
use std::{
    io::{Read, Seek, SeekFrom},
    net::SocketAddr,
    path::{Path, PathBuf},
    process::Command,
    sync::Arc,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::sync::{broadcast, Mutex};

mod collector;
mod health;
mod ledger;

const TAIL: usize = 12;
const LOG_WINDOW: u64 = 4_000_000;
const JSON_LIMIT: u64 = 2_000_000;
const SCAN_MAX_AGE: u64 = 3 * 60 * 60;

#[derive(Clone)]
struct AppState {
    tx: broadcast::Sender<String>,
    current: Arc<Mutex<String>>,
    root: PathBuf,
    port: u16,
    /// Equity/balance history. In memory only: the engine keeps no time series,
    /// and a monitor that wrote its own state files into the engine's tree would
    /// be a second writer. Nothing here survives a restart, and the UI says so.
    series: Arc<Mutex<collector::Collector>>,
    /// When POST /api/scan last started a scan. The button has a client-side
    /// guard; a local process does not, and every extra POST spawns another
    /// interpreter that fights the funnel lock and churns the terminal.
    last_scan: Arc<std::sync::Mutex<LastScan>>,
    /// A child is between spawn and exit. The five-second debounce cannot answer
    /// "is a scan still running" - a scan takes minutes - so the second click
    /// gets a 409 instead of a second interpreter.
    scan_running: Arc<std::sync::atomic::AtomicBool>,
    /// The last child's outcome. `started:true` only ever meant "spawned"; a
    /// scan that no-oped on the lock in 0.3 s reported success. The exit code is
    /// the only honest signal, so it is recorded and served.
    last_run: Arc<std::sync::Mutex<Option<Value>>>,
    /// Connected sockets. Snapshots are 130-300 KB cloned per client per beat;
    /// an unbounded count is unbounded local memory.
    clients: Arc<std::sync::atomic::AtomicUsize>,
}

/// Append one line to the monitor's own journal. Not the engine's ledger: a
/// second writer there would break its accounting, so this file is the
/// dashboard's and holds only what the dashboard did.
fn journal(root: &Path, row: Value) {
    use std::io::Write;
    let path = root.join("dashboard_scan.jsonl");
    let Ok(f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
    else {
        return; // a journal that cannot be written must not fail a scan
    };
    let _ = writeln!(&f, "{row}");
}

/// A stable digest of the embedded diagram, computed once per process.
fn architecture_etag() -> &'static String {
    static TAG: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    TAG.get_or_init(|| {
        use std::hash::{Hash, Hasher};
        let body = include_str!("../web/architecture.html");
        let mut h = std::collections::hash_map::DefaultHasher::new();
        body.hash(&mut h);
        format!("\"{}:{:x}\"", body.len(), h.finish())
    })
}

/// ISO-8601 UTC, second resolution: the same shape the engine's artifacts use.
fn now_utc() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0) as i64;
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    // Civil-from-days, Howard Hinnant's algorithm: no date crate for one stamp.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60
    )
}

/// `None` means no scan has started in this process.
///
/// It used to be `now - 1h`, which is only expressible while the machine has been
/// up for an hour: `checked_sub` underflows on a fresh boot, and the fallback made
/// "never scanned" indistinguishable from "scanned a moment ago" - the first click
/// of the day answered 429.
type LastScan = Option<std::time::Instant>;

fn tail(path: &Path, n: usize) -> Option<Vec<String>> {
    // Reads a bounded window from the end instead of the whole file. The ledger
    // is append-only and grows forever; `read_to_string` on it every second is
    // the one place this monitor's cost scaled with history rather than with
    // what it displays. 16 KiB holds a dozen lines at any realistic width.
    //
    // None means the file is absent or unreadable; Some(vec![]) means it exists
    // and is empty. Those are different facts: a missing ledger is usually a
    // wrong ZTT_ROOT, and rendering it as "no runs yet" hides that.
    const WINDOW: u64 = 16 * 1024;
    let mut f = std::fs::File::open(path).ok()?;
    let len = f.metadata().ok()?.len();
    let start = len.saturating_sub(WINDOW);
    if start > 0 {
        f.seek(SeekFrom::Start(start)).ok()?;
    }
    let mut bytes = Vec::with_capacity((len - start) as usize);
    f.take(WINDOW).read_to_end(&mut bytes).ok()?;
    let text = String::from_utf8_lossy(&bytes);
    // A cut at `start` lands mid-line; that first fragment is not a row.
    let body = if start > 0 {
        match text.find('\n') {
            Some(i) => &text[i + 1..],
            None => "",
        }
    } else {
        &text[..]
    };
    let lines: Vec<&str> = body.lines().filter(|l| !l.trim().is_empty()).collect();
    Some(
        lines[lines.len().saturating_sub(n)..]
            .iter()
            .map(|l| l.to_string())
            .collect(),
    )
}

fn age_secs(path: &Path) -> Option<u64> {
    let m = std::fs::metadata(path).ok()?.modified().ok()?;
    SystemTime::now()
        .duration_since(m)
        .ok()
        .map(|d| d.as_secs())
}

/// The supervisor publishes a NEW state generation each tick and never touches
/// a file a reader holds: that is what keeps WinError 5 off the write path.
/// The reader's side of that contract is to resolve the newest generation.
/// Falls back to the legacy fixed name so an older tree still renders.
fn newest_state(dir: &Path) -> PathBuf {
    let mut best: Option<(SystemTime, PathBuf)> = None;
    if let Ok(entries) = std::fs::read_dir(dir) {
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if !(name.starts_with("engine_state.") && name.ends_with(".json")) {
                continue;
            }
            // ponytail: newest of two files, scanned once a second. An index
            // would cost more than it saves at this size.
            if let Ok(modified) = entry.metadata().and_then(|m| m.modified()) {
                if best.as_ref().map(|(t, _)| modified > *t).unwrap_or(true) {
                    best = Some((modified, entry.path()));
                }
            }
        }
    }
    best.map(|(_, p)| p)
        .unwrap_or_else(|| dir.join("engine_state.json"))
}

/// The bridge writes audit.jsonl next to its own config (pre-migration) or in
/// the tree; both readers must resolve it the same way or the pane shows lines
/// the Copy button cannot fetch.
fn resolve_audit(root: &Path) -> Option<PathBuf> {
    [
        root.join("audit.jsonl"),
        std::env::var("APPDATA")
            .map(|p| PathBuf::from(p).join("mt5-mcp").join("audit.jsonl"))
            .unwrap_or_default(),
    ]
    .into_iter()
    .find(|p| !p.as_os_str().is_empty() && p.exists())
}

/// Keep only the fields the UI reads, and cap the arrays it renders.
///
/// `null` passes through untouched: a missing artifact must stay "unknown", not
/// become an empty object.
fn trim_stage(doc: &Value, keep: &[&str], arrays: &[&str], cap: usize) -> Value {
    if !doc.is_object() {
        return doc.clone();
    }
    let mut out = serde_json::Map::new();
    for key in keep {
        let Some(value) = doc.get(*key) else { continue };
        let value = if arrays.contains(key) {
            match value.as_array() {
                Some(rows) => Value::Array(rows.iter().rev().take(cap).rev().cloned().collect()),
                None => value.clone(),
            }
        } else {
            value.clone()
        };
        out.insert((*key).to_string(), value);
    }
    Value::Object(out)
}

/// Null when the file is absent or mid-write: the snapshot must never fail
/// because a producer was between `open()` and `write()`.
/// Parsed documents, keyed by path, invalidated by (mtime, len).
///
/// The watch loop rebuilds the snapshot every second and re-parsed every input
/// each beat. A stat costs microseconds; parsing two megabytes of JSON does not.
type JsonCache =
    std::sync::Mutex<std::collections::HashMap<PathBuf, (std::time::SystemTime, u64, Value)>>;

fn json_cache() -> &'static JsonCache {
    static CACHE: std::sync::OnceLock<JsonCache> = std::sync::OnceLock::new();
    CACHE.get_or_init(|| std::sync::Mutex::new(std::collections::HashMap::new()))
}

fn read_json(path: &Path) -> Value {
    let stamp = std::fs::metadata(path)
        .ok()
        .and_then(|m| m.modified().ok().map(|t| (t, m.len())));
    if let Some(stamp) = stamp {
        if let Ok(cache) = json_cache().lock() {
            if let Some((t, len, doc)) = cache.get(path) {
                if *t == stamp.0 && *len == stamp.1 {
                    return doc.clone();
                }
            }
        }
    }
    let doc = read_bounded(path, JSON_LIMIT)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or(Value::Null);
    if let Some(stamp) = stamp {
        if let Ok(mut cache) = json_cache().lock() {
            cache.insert(path.to_path_buf(), (stamp.0, stamp.1, doc.clone()));
        }
    }
    doc
}

fn read_bounded(path: &Path, limit: u64) -> std::io::Result<String> {
    let file = std::fs::File::open(path)?;
    let mut bytes = Vec::new();
    file.take(limit + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > limit {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "file exceeds read limit",
        ));
    }
    String::from_utf8(bytes)
        .map_err(|_| std::io::Error::new(std::io::ErrorKind::InvalidData, "invalid UTF-8"))
}

fn display_engine(mut engine: Value) -> Value {
    let Some(object) = engine.as_object_mut() else {
        return Value::Null;
    };
    object.retain(|k, _| ["ok", "applied", "error", "positions", "account"].contains(&k.as_str()));
    let ok_flag = object.get("ok").and_then(Value::as_bool);
    let positions_valid = object
        .get("positions")
        .and_then(Value::as_array)
        .is_some_and(|items| items.iter().all(Value::is_object));
    if ok_flag != Some(true) || !positions_valid {
        if ok_flag != Some(false) {
            object.insert("ok".into(), json!(false));
            object.insert(
                "error".into(),
                json!(if ok_flag.is_none() {
                    "invalid engine observation: missing ok"
                } else {
                    "invalid position observation"
                }),
            );
        }
        object.insert("positions".into(), Value::Null);
    }
    if let Some(account) = object.get_mut("account").and_then(Value::as_object_mut) {
        let label = account
            .get("login")
            .and_then(Value::as_u64)
            .and_then(|login| {
                let text = login.to_string();
                (text.len() >= 3).then(|| format!("account •••{}", &text[text.len() - 3..]))
            });
        account.retain(|k, _| ["equity", "balance", "currency", "server"].contains(&k.as_str()));
        account.insert("label".into(), json!(label));
    }
    if let Some(positions) = object.get_mut("positions").and_then(Value::as_array_mut) {
        for position in positions {
            if let Some(fields) = position.as_object_mut() {
                fields.retain(|k, _| {
                    ["symbol", "side", "volume", "entry", "price", "sl", "profit"]
                        .contains(&k.as_str())
                });
            }
        }
    }
    engine
}

fn scan_state(root: &Path, tier2: &Value, tier3: &Value, funnel: Option<&Vec<String>>) -> Value {
    let row = funnel
        .and_then(|lines| lines.last())
        .map(String::as_str)
        .unwrap_or("");
    let ledger_id = row
        .split_whitespace()
        .find_map(|word| word.strip_prefix("scan_id="));
    let t2_id = tier2
        .get("scan_id")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty());
    let t3_id = tier3
        .get("scan_id")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty());
    // Same vocabulary the ledger parser uses, so "current" cannot disagree with
    // what the ledger page shows: a WINNER row ("WINNER:SYM,...") is a completed
    // scan exactly like STAND_DOWN. The writer emits no other verdict.
    let verdict = ledger::verdict_word(row.split(" | ").nth(2).unwrap_or(""));
    let ids_match = t2_id.is_some() && t2_id == t3_id && t2_id == ledger_id;
    let fresh = ["tier2_candidates.json", "tier3_results.json"]
        .iter()
        .all(|name| age_secs(&root.join(name)).is_some_and(|age| age <= SCAN_MAX_AGE));
    let current = ids_match && fresh && matches!(verdict, Some("STAND_DOWN" | "WINNER"));
    let reason = if !ids_match {
        "missing or mismatched scan identity"
    } else if !fresh {
        "scan artifacts are older than three hours"
    } else if !current {
        "latest scan is incomplete or failed"
    } else {
        "completed coherent scan"
    };
    json!({"current": current, "status": if current {"current"} else {"historical_or_unknown"},
           "scan_id": ledger_id, "reason": reason})
}

fn build(root: &Path, series: &mut collector::Collector, last_scan_run: Value) -> Value {
    // The same marker position_manager and run_funnel refuse on. The monitor
    // reads it and nothing else: a UI that could set it would be a control
    // surface, and this process has exactly one write already.
    let kill_switch = if std::env::var("ZTT_KILL_SWITCH")
        .map(|v| v.trim() == "1")
        .unwrap_or(false)
    {
        Some("ZTT_KILL_SWITCH=1 in the environment".to_string())
    } else if root.join("KILL_SWITCH").exists() {
        Some("KILL_SWITCH exists at the tree root".to_string())
    } else {
        None
    };

    let engine_path = newest_state(root);
    let engine = display_engine(read_json(&engine_path));

    // None, not 0: no engine state means we do not know the position count.
    // Reporting 0 there is the difference between "flat" and "blind".
    let positions = engine
        .get("positions")
        .and_then(Value::as_array)
        .map(|v| v.len());
    let equity = engine.get("account").and_then(|a| a.get("equity")).cloned();
    let balance = engine
        .get("account")
        .and_then(|a| a.get("balance"))
        .cloned();
    let currency = engine
        .get("account")
        .and_then(|a| a.get("currency"))
        .cloned()
        .unwrap_or(json!(""));
    let server = engine.get("account").and_then(|a| a.get("server")).cloned();

    let mcp = resolve_audit(root).and_then(|p| tail(&p, TAIL));

    // The two stage artifacts the funnel leaves behind. Absent outside a scan
    // window, which is the normal state most of the day.
    //
    // Projected, not shipped whole: the raw documents reach 2 MB each, ride every
    // snapshot string, sit 32-deep in the broadcast ring and are cloned per
    // client per second. The keep-lists are exactly the fields the UI reads
    // (`t2.*` / `t3.*` in index.html) — nothing here is guessed.
    let tier2 = trim_stage(
        &read_json(&root.join("tier2_candidates.json")),
        &[
            "scan_id",
            "generated_utc",
            "symbols_in",
            "symbols_tested",
            "total_tests",
            "triggering",
            "per_family",
            "corr_max",
            "deduped",
            "all_hits",
        ],
        &["all_hits"],
        20,
    );
    let tier3 = trim_stage(
        &read_json(&root.join("tier3_results.json")),
        &[
            "scan_id",
            "generated_utc",
            "trials_declared",
            "trials_cumulative",
            "z_crit_corrected",
            "corr_max",
            "passed_both_gates",
            "survivors",
            "stood_down",
            "results",
        ],
        // `results` is the shortlist, bounded by --shortlist, and the UI derives its
        // length from the array - capping it would report 20 for a longer run.
        &[],
        20,
    );
    let agent = read_json(&root.join("agent_notes.json"));
    // Derived from desk_history.sqlite by desk_store.export(): the last runs, one
    // object each. The database is the record; this is the view the UI can read
    // without a SQLite driver in the dashboard binary.
    let desk_history = read_json(&root.join("desk_history.json"));
    // Where the running scan is. Written by run_funnel.progress() at each stage
    // boundary; absent when no scan has ever run on this tree.
    let progress = read_json(&root.join("scan_progress.json"));
    let funnel = tail(&root.join("funnel_history.log"), TAIL);
    let scan = scan_state(root, &tier2, &tier3, funnel.as_ref());

    // One observation per build. This is the only place the dashboard gains
    // memory; it writes nothing.
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    series.sample(
        now,
        equity.as_ref().and_then(Value::as_f64),
        balance.as_ref().and_then(Value::as_f64),
    );

    let ledger_doc = ledger::snapshot(&root.join("funnel_history.log"));
    if let Some(row) = ledger_doc
        .get("rows")
        .and_then(Value::as_array)
        .and_then(|r| r.first())
    {
        let verdict = row.get("verdict").and_then(Value::as_str).unwrap_or("");
        if !verdict.is_empty() {
            series.event(
                now,
                "verdict",
                verdict,
                row.get("scan_id").and_then(Value::as_str),
            );
        }
    }

    let engine_age_secs = age_secs(&engine_path);
    let maintenance = health::maintenance(root);
    // The engine publishes generation-suffixed state files, so a plain-name check
    // reports "absent" while the supervisor is running. Report what was resolved.
    let mut health_files = health::files(root);
    if let Some(map) = health_files.as_object_mut() {
        map.insert(
            "engine_state.json".to_string(),
            json!({"exists": engine_age_secs.is_some(), "age_s": engine_age_secs}),
        );
    }
    let alerts = health::alerts(
        engine_age_secs.is_some(),
        engine_age_secs.map(|a| a as i64),
        engine.get("ok").and_then(Value::as_bool).unwrap_or(false),
        engine.get("error").and_then(Value::as_str),
        ledger_doc
            .get("rows")
            .and_then(Value::as_array)
            .and_then(|r| r.first()),
        Some(&agent),
        &maintenance,
        ledger_doc
            .get("present")
            .and_then(Value::as_bool)
            .unwrap_or(false),
    );

    json!({
        // Bumped whenever a field changes meaning or disappears. Additive fields do
        // not bump it: a client that does not know a key is already correct.
        "schema_version": SCHEMA_VERSION,
        "kill_switch": kill_switch,
        "ts": SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0),
        "engine": engine,
        "engine_age_s": age_secs(&engine_path),
        "tier2": tier2,
        "tier3": tier3,
        "agent": agent,
        "desk_history": desk_history,
        "progress": progress,
        "scan": scan,
        "equity": equity,
        "balance": balance,
        "currency": currency,
        "server": server,
        "positions": positions,
        "funnel": funnel,
        "actions": tail(&root.join("position_manager.log"), TAIL),
        "mcp": mcp,
        "universe": universe(root),
        // What the button actually did last: `started` means spawned; this says
        // how it ended, which is the only signal a human can act on.
        "last_scan_run": last_scan_run,
        "ledger": ledger_doc,
        "series": series.snapshot(),
        "health": json!({
            "files": health_files,
            "maintenance": maintenance,
        }),
        "alerts": alerts,
    })
}

/// The liquid universe, trimmed to what the tiles need. Bounded read: the file is
/// ~100 KB, but the reader is the same bounded one every other artifact uses.
fn universe(root: &Path) -> Value {
    let doc = read_json(&root.join("liquid_universe.json"));
    let symbols = doc.get("symbols").and_then(Value::as_array);
    let Some(symbols) = symbols else {
        // Absent is absent. A universe of [] is a rebuild that failed, and the
        // UI must be able to tell those apart, so it is passed through as-is.
        return json!({"present": false, "count": null, "generated_utc": null, "symbols": []});
    };
    let rows: Vec<Value> = symbols
        .iter()
        .map(|s| {
            json!({
                "symbol": s.get("symbol").cloned().unwrap_or(Value::Null),
                "class": s.get("class").cloned().unwrap_or(Value::Null),
                "spread_pct_atr": s.get("spread_pct_atr").cloned().unwrap_or(Value::Null),
                "mean_tick_volume": s.get("mean_tick_volume").cloned().unwrap_or(Value::Null),
                // The heatmap needs a few more readings per symbol, and the builder
                // already measured them: friction, history depth and staleness.
                "h1_days": s.get("h1_days").cloned().unwrap_or(Value::Null),
                "bar_age_min": s.get("bar_age_min").cloned().unwrap_or(Value::Null),
            })
        })
        .collect();
    json!({
        "present": true,
        "count": doc.get("count").cloned().unwrap_or(json!(rows.len())),
        "generated_utc": doc.get("generated_utc").cloned().unwrap_or(Value::Null),
        "by_class": doc.get("by_class").cloned().unwrap_or(Value::Null),
        "symbols": rows,
    })
}

async fn watch(state: AppState) {
    let mut last = String::new();
    let mut tick = tokio::time::interval(Duration::from_secs(1));
    tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    let mut beats: u64 = 0;
    loop {
        tick.tick().await;
        beats += 1;
        let root = state.root.clone();
        let series = state.series.clone();
        // Read once per beat: the button's outcome is part of the snapshot the
        // UI renders, so a scan that ended at exit 2 is visible without a reload.
        let run = state
            .last_run
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
            .unwrap_or(Value::Null);
        let built = tokio::task::spawn_blocking(move || {
            let mut guard = series.blocking_lock();
            serde_json::to_string(&build(&root, &mut guard, run))
        })
        .await;

        let body = match built {
            Ok(Ok(body)) => body,
            // A failed build used to end this task, which silently stopped the
            // feed: every client then sat on an open socket that would never
            // speak again. One bad read is not a reason to go dark — keep the
            // last good snapshot and try again on the next tick.
            Ok(Err(e)) => {
                eprintln!("snapshot serialisation failed: {e}");
                continue;
            }
            Err(e) => {
                eprintln!("snapshot task failed: {e}");
                continue;
            }
        };

        // Send when the body changed, and once every five seconds regardless.
        // A static tree must still produce traffic, or a client cannot tell
        // "nothing changed" from "the feed died" — and the 5 s freshness
        // watchdog would call a healthy socket stale.
        let changed = body != last;
        // 4 s, not 5: the client's staleness threshold is 8 s, and an equal
        // threshold turns a healthy socket into a reconnecting one on jitter.
        if beats.is_multiple_of(5) {
            // The monitor's own liveness, written where status.py can read it.
            // Nothing restarts it yet; nothing can report a death it cannot see.
            let beat = json!({"ts": now_utc(), "pid": std::process::id(),
                              "clients": state.clients.load(std::sync::atomic::Ordering::SeqCst)});
            let tmp = state.root.join("dashboard_heartbeat.json.tmp");
            let path = state.root.join("dashboard_heartbeat.json");
            if std::fs::write(&tmp, beat.to_string()).is_ok() {
                let _ = std::fs::rename(&tmp, &path);
            }
        }
        if changed || beats.is_multiple_of(4) {
            last = body.clone();
            *state.current.lock().await = body.clone();
            let _ = state.tx.send(body);
        }
    }
}

async fn latest_report(State(st): State<AppState>) -> impl IntoResponse {
    // The per-scan HTML report is the deep view; this is the bridge to it from
    // the live view. Newest file wins -- one scan, one page.
    let read = tokio::task::spawn_blocking(move || latest_report_body(&st.root)).await;
    match read {
        Ok(Ok(Some(body))) => (
            StatusCode::OK,
            [
                (header::CONTENT_TYPE, "text/html; charset=utf-8"),
                // The report is a producer artifact. If it ever grew a <script>,
                // it would run in the dashboard's origin with access to the
                // snapshot and the scan trigger. `report.py` emits inline styles
                // only, so this policy never bites - and forbids the day it does.
                (
                    header::CONTENT_SECURITY_POLICY,
                    // `frame-ancestors` too: the middleware only fills an absent
                    // policy, so this page was the one HTML a foreign site could
                    // frame - an inconsistent edge of a deliberate rule.
                    "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; sandbox",
                ),
            ],
            body,
        )
            .into_response(),
        Ok(Ok(None)) => (
            StatusCode::NOT_FOUND,
            [(header::CONTENT_TYPE, "text/plain; charset=utf-8")],
            String::from("no reports yet - the next scan writes one"),
        )
            .into_response(),
        _ => (
            StatusCode::SERVICE_UNAVAILABLE,
            [(header::CONTENT_TYPE, "text/plain; charset=utf-8")],
            String::from("latest report is unreadable or exceeds the 8 MB limit"),
        )
            .into_response(),
    }
}

fn latest_report_body(root: &Path) -> std::io::Result<Option<String>> {
    let dir = root.join("reports");
    let mut newest: Option<(std::time::SystemTime, std::path::PathBuf)> = None;
    let entries = match std::fs::read_dir(&dir) {
        Ok(entries) => entries,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e),
    };
    for e in entries {
        let e = e?;
        let p = e.path();
        if p.extension().and_then(|s| s.to_str()) != Some("html") {
            continue;
        }
        if let Ok(m) = e.metadata().and_then(|m| m.modified()) {
            let newer = match &newest {
                None => true,
                Some((t, _)) => m > *t,
            };
            if newer {
                newest = Some((m, p));
            }
        }
    }
    newest
        .map(|(_, path)| read_bounded(&path, 8_000_000))
        .transpose()
}

fn log_excerpt(path: &Path, limit: u64) -> std::io::Result<(String, bool, u64)> {
    let mut file = std::fs::File::open(path)?;
    let total = file.metadata()?.len();
    let start = total.saturating_sub(limit);
    file.seek(SeekFrom::Start(start))?;
    let mut bytes = Vec::new();
    file.take(limit).read_to_end(&mut bytes)?;
    let text = String::from_utf8_lossy(&bytes);
    let body = if start > 0 {
        text.split_once('\n').map(|(_, rest)| rest).unwrap_or("")
    } else {
        &text
    };
    Ok((body.to_string(), start > 0, total))
}

async fn full_log(AxumPath(which): AxumPath<String>, State(st): State<AppState>) -> Response {
    let path = match which.as_str() {
        "funnel" => Some(st.root.join("funnel_history.log")),
        "actions" => Some(st.root.join("position_manager.log")),
        "audit" => resolve_audit(&st.root),
        _ => return (StatusCode::NOT_FOUND, "unknown log").into_response(),
    };
    // A missing file must not answer 200 with "": the Copy button would flash
    // "copied" and hand the operator an empty clipboard.
    //
    // This is the only read that can be large, so it leaves the reactor:
    // spawn_blocking keeps a 4 MB copy from stalling the WebSocket heartbeat.
    let Some(path) = path else {
        return (StatusCode::NOT_FOUND, "log file not found").into_response();
    };
    let read = tokio::task::spawn_blocking(move || log_excerpt(&path, LOG_WINDOW)).await;
    match read {
        Ok(Ok((text, truncated, total))) => (
            StatusCode::OK,
            [
                (
                    header::CONTENT_TYPE.as_str(),
                    "text/plain; charset=utf-8".to_string(),
                ),
                ("x-log-truncated", truncated.to_string()),
                ("x-log-bytes-total", total.to_string()),
                ("x-log-window-bytes", LOG_WINDOW.to_string()),
            ],
            text,
        )
            .into_response(),
        Ok(Err(e)) if e.kind() == std::io::ErrorKind::NotFound => {
            (StatusCode::NOT_FOUND, "log file not found").into_response()
        }
        _ => (StatusCode::SERVICE_UNAVAILABLE, "log file unreadable").into_response(),
    }
}

/// The Archify diagram of this pipeline, committed as a static artifact.
/// Embedded rather than read from disk: it is a build input, and a monitor that
/// serves a missing diagram as a blank page would be the same lie as a zero.
async fn architecture() -> impl IntoResponse {
    // The diagram is 798 KB of generator output with four script blocks. It used
    // to run with the whole origin's authority, one route away from the report
    // page that was given a sandbox for exactly this reason. `sandbox
    // allow-scripts` (no allow-same-origin) puts it in an opaque origin: its
    // viewer still pans and exports, and any fetch it made to /api/snapshot
    // arrives with Origin: null, which local_headers refuses.
    (
        [
            (header::CONTENT_TYPE, "text/html; charset=utf-8"),
            (
                header::CONTENT_SECURITY_POLICY,
                "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; \
                 font-src data:; img-src data:; sandbox allow-scripts",
            ),
        ],
        Html(include_str!("../web/architecture.html")),
    )
}

async fn index() -> Html<&'static str> {
    Html(include_str!("../web/index.html"))
}

async fn js(which: &str) -> impl IntoResponse {
    let body = match which {
        "react" => include_str!("../web/vendor/react.production.min.js"),
        "react-dom" => include_str!("../web/vendor/react-dom.production.min.js"),
        _ => include_str!("../web/vendor/htm.umd.js"),
    };
    (
        [(axum::http::header::CONTENT_TYPE, "application/javascript")],
        body,
    )
}

async fn snapshot(State(st): State<AppState>) -> impl IntoResponse {
    (
        [(header::CONTENT_TYPE, "application/json")],
        st.current.lock().await.clone(),
    )
}

fn local_headers(headers: &HeaderMap, port: u16, websocket: bool) -> bool {
    let authorities = [format!("localhost:{port}"), format!("127.0.0.1:{port}")];
    let single = |name| headers.get_all(name).iter().count() <= 1;
    let host_ok = headers
        .get(header::HOST)
        .and_then(|h| h.to_str().ok())
        .is_some_and(|host| authorities.iter().any(|allowed| allowed == host));
    let origin_ok = match headers.get(header::ORIGIN) {
        Some(origin) => origin.to_str().ok().is_some_and(|origin| {
            authorities
                .iter()
                .any(|host| origin == format!("http://{host}"))
        }),
        None => !websocket,
    };
    single(header::HOST) && single(header::ORIGIN) && host_ok && origin_ok
}

async fn local_request(State(st): State<AppState>, request: Request, next: Next) -> Response {
    if !local_headers(request.headers(), st.port, request.uri().path() == "/ws") {
        return (StatusCode::FORBIDDEN, "local Host and Origin required").into_response();
    }
    let path = request.uri().path().to_string();
    let inm = request
        .headers()
        .get(header::IF_NONE_MATCH)
        .and_then(|v| v.to_str().ok())
        .map(str::to_owned);
    let mut response = next.run(request).await;
    let cache = if path.starts_with("/vendor/") {
        // Hash-pinned bytes, verified against PROVENANCE.json in CI: they cannot
        // change without the manifest changing, so `no-store` on them was 1.15 MB
        // re-sent per reload for no reason.
        "public, max-age=31536000, immutable"
    } else {
        "no-store"
    };
    for (name, value) in [
        (header::CACHE_CONTROL, cache),
        (header::X_CONTENT_TYPE_OPTIONS, "nosniff"),
    ] {
        response.headers_mut().insert(name, value.parse().unwrap());
    }
    // `insert` here used to overwrite the report route's own sandbox policy with
    // `frame-ancestors 'none'`, so the backstop the code argued for was inert on
    // every response. Only set it when the handler did not.
    if path == "/architecture" {
        let tag = architecture_etag();
        response
            .headers_mut()
            .insert(header::ETAG, tag.parse().unwrap());
        if inm.as_deref() == Some(tag.as_str()) {
            *response.status_mut() = StatusCode::NOT_MODIFIED;
            *response.body_mut() = axum::body::Body::empty();
        }
    }
    if !response
        .headers()
        .contains_key(header::CONTENT_SECURITY_POLICY)
    {
        response.headers_mut().insert(
            header::CONTENT_SECURITY_POLICY,
            "frame-ancestors 'none'".parse().unwrap(),
        );
    }
    response
}

/// Exit 0 alone cannot tell "ran" from "no-op": the funnel returns 0 when it
/// loses the lock or is outside its window. A completed scan appends a ledger
/// row; a no-op leaves the ledger unchanged.
fn scan_outcome(exit: Option<i32>, ledger_before: u64, ledger_after: u64) -> &'static str {
    match exit {
        Some(0) if ledger_after > ledger_before => "completed",
        Some(0) => "noop",
        _ => "failed",
    }
}

/// POST /api/scan — the one write in this process.
///
/// It starts the *research* pipeline and nothing else. `run_funnel.py` takes its
/// own lock (a double click costs 0.3 s and exits), enforces the UTC window, runs
/// the demo-only pre-flight, and cannot place an order: order entry lives in
/// `position_manager.py` behind a human APPROVED and the bridge consent gate.
/// There is deliberately no argument: the button runs exactly what the scheduler
/// runs, so the UI cannot become a second, differently-configured runner.
async fn trigger_scan(State(st): State<AppState>) -> Response {
    // `python` on PATH is not necessarily this project's interpreter (on this
    // machine it is a tool venv without MetaTrader5). The Windows launcher `py -3`
    // is the portable way to reach the installed 3.x that has the terminal module;
    // ZTT_PYTHON overrides both.
    // ZTT_PYTHON wins; then the project's own venv, which is the interpreter the
    // scheduler runs (`start_dashboard.bat` pins it); only then the launcher's
    // best guess. The guess has no MetaTrader5 on this machine, so a scan started
    // through it fails closed on import and reports FAILED for the wrong reason.
    let override_python = std::env::var("ZTT_PYTHON").ok();
    let venv = if cfg!(windows) {
        st.root.join(".venv").join("Scripts").join("python.exe")
    } else {
        st.root.join(".venv").join("bin").join("python")
    };
    let venv_probe = venv.clone();
    let venv_present = tokio::task::spawn_blocking(move || venv_probe.exists())
        .await
        .unwrap_or(false);
    let (program, needs_flag) = match override_python {
        Some(p) => (p, false),
        None if venv_present => (venv.display().to_string(), false),
        None => (
            (if cfg!(windows) { "py" } else { "python3" }).to_string(),
            cfg!(windows),
        ),
    };
    let mut interpreter = Command::new(program);
    if needs_flag {
        interpreter.arg("-3");
    }
    use std::sync::atomic::Ordering;
    if st.scan_running.load(Ordering::SeqCst) {
        journal(
            &st.root,
            json!({"ts": now_utc(), "event": "post", "outcome": 409,
                                 "reason": "a scan is still running"}),
        );
        return (
            StatusCode::CONFLICT,
            Json(json!({"started": false, "error": "a scan is still running",
                        "code": "scan_running", "status": 409})),
        )
            .into_response();
    }
    {
        let last = st.last_scan.lock().unwrap_or_else(|e| e.into_inner());
        if last
            .map(|t| t.elapsed() < Duration::from_secs(5))
            .unwrap_or(false)
        {
            journal(
                &st.root,
                json!({"ts": now_utc(), "event": "post", "outcome": 429,
                                     "reason": "inside the 5 s debounce"}),
            );
            return (
                StatusCode::TOO_MANY_REQUESTS,
                Json(json!({"started": false, "error": "scan already starting",
                                "code": "debounced", "status": 429})),
            )
                .into_response();
        }
    }
    let script = st.root.join("run_funnel.py");
    // An async handler must not block the reactor on a stat call.
    let probe = script.clone();
    let exists = tokio::task::spawn_blocking(move || probe.exists())
        .await
        .unwrap_or(false);
    if !exists {
        journal(
            &st.root,
            json!({"ts": now_utc(), "event": "post", "outcome": 503,
                                 "reason": "run_funnel.py not found"}),
        );
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"started": false, "error": "run_funnel.py not found",
                        "code": "script_missing", "status": 503})),
        )
            .into_response();
    }
    let ledger_path = st.root.join("funnel_history.log");
    let ledger_before = std::fs::metadata(&ledger_path)
        .map(|m| m.len())
        .unwrap_or(0);
    // The load above is only a fast path. Two concurrent POSTs both passed it
    // before either had spawned, and both spawned. Claiming the slot is one
    // atomic step now; the loser is refused instead of starting a second
    // interpreter that fights the funnel lock and churns the terminal.
    if st
        .scan_running
        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .is_err()
    {
        journal(
            &st.root,
            json!({"ts": now_utc(), "event": "post", "outcome": 409,
                                 "reason": "a scan is still running"}),
        );
        return (
            StatusCode::CONFLICT,
            Json(json!({"started": false, "error": "a scan is still running",
                        "code": "scan_running", "status": 409})),
        )
            .into_response();
    }
    match interpreter
        .arg(&script)
        .arg("--window-utc")
        .arg("05:00-17:00")
        .arg("--t-gate")
        .current_dir(&st.root)
        .stdin(std::process::Stdio::null())
        .spawn()
    {
        Ok(mut child) => {
            let pid = child.id();
            journal(
                &st.root,
                json!({"ts": now_utc(), "event": "start", "pid": pid}),
            );
            // Stamped only after the spawn succeeded: a 503 (script missing) used
            // to consume the debounce, so a corrected retry answered 429 when
            // nothing had started.
            *st.last_scan.lock().unwrap_or_else(|e| e.into_inner()) =
                Some(std::time::Instant::now());
            {
                let mut run = st.last_run.lock().unwrap_or_else(|e| e.into_inner());
                *run = Some(
                    json!({"pid": pid, "started_utc": now_utc(), "exit": Value::Null,
                                   "running": true}),
                );
            }
            // Without wait() every finished scan is a zombie on Unix, and the exit
            // status - the only honest "it ran" signal - is discarded. `started`
            // only ever meant "spawned": a scan that no-oped on the lock in 0.3 s
            // reported success to the UI.
            let running = st.scan_running.clone();
            let last_run = st.last_run.clone();
            let journal_root = st.root.clone();
            std::thread::spawn(move || {
                let status = child.wait();
                running.store(false, Ordering::SeqCst);
                let exit = status.ok().and_then(|s| s.code());
                let ledger_after = std::fs::metadata(&ledger_path)
                    .map(|m| m.len())
                    .unwrap_or(0);
                let mut run = last_run.lock().unwrap_or_else(|e| e.into_inner());
                let mut doc = run.take().unwrap_or_else(|| json!({"pid": pid}));
                journal(
                    &journal_root,
                    json!({"ts": now_utc(), "event": "exit", "pid": pid, "exit": exit}),
                );
                if let Some(obj) = doc.as_object_mut() {
                    obj.insert("exit".into(), json!(exit));
                    obj.insert(
                        "outcome".into(),
                        json!(scan_outcome(exit, ledger_before, ledger_after)),
                    );
                    obj.insert("running".into(), json!(false));
                    obj.insert("finished_utc".into(), json!(now_utc()));
                }
                *run = Some(doc);
            });
            (
                StatusCode::ACCEPTED,
                Json(json!({"started": true, "pid": pid, "code": "started"})),
            )
                .into_response()
        }
        Err(e) => {
            // Nothing started: the claimed slot must be free again, or a broken
            // interpreter would refuse every later POST until a restart.
            st.scan_running.store(false, Ordering::SeqCst);
            journal(
                &st.root,
                json!({"ts": now_utc(), "event": "post", "outcome": 500,
                                     "reason": e.to_string()}),
            );
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"started": false, "error": e.to_string(),
                        "code": "spawn_failed", "status": 500})),
            )
                .into_response()
        }
    }
}

async fn ws(ws: WebSocketUpgrade, State(st): State<AppState>) -> impl IntoResponse {
    // Every client gets its own clone of a 130-300 KB snapshot up to once a
    // second. A cap is the difference between a monitor and an accidental
    // memory leak with an open socket attached.
    const MAX_CLIENTS: usize = 16;
    if st.clients.load(std::sync::atomic::Ordering::SeqCst) >= MAX_CLIENTS {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            "too many dashboard clients",
        )
            .into_response();
    }
    st.clients.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    // This socket carries snapshots server->client. Inbound frames are ignored,
    // so their only possible use is to allocate memory: 64 KiB is generous.
    let failed = st.clone();
    ws.max_message_size(64 * 1024)
        .on_failed_upgrade(move |_| {
            // The count is incremented before the 101 is written. Without this,
            // sixteen clients that die inside that window brick the monitor for
            // every later client until the process restarts.
            failed
                .clients
                .fetch_sub(1, std::sync::atomic::Ordering::SeqCst);
        })
        .on_upgrade(move |socket| client(socket, st))
        .into_response()
}

async fn client(mut socket: WebSocket, st: AppState) {
    struct Counted(AppState);
    impl Drop for Counted {
        fn drop(&mut self) {
            self.0
                .clients
                .fetch_sub(1, std::sync::atomic::Ordering::SeqCst);
        }
    }
    let _counted = Counted(st.clone());
    let mut rx = st.tx.subscribe();
    let first = st.current.lock().await.clone();
    if socket.send(Message::Text(first.into())).await.is_err() {
        return;
    }
    // A browser cannot send a WebSocket ping itself, and a socket that carries
    // nothing for minutes is the one an idle NAT or a laptop sleep silently
    // kills — leaving a half-open connection that never fires `onclose`, so the
    // page sat "connected" while the feed was gone. The server pings; a failed
    // ping ends the task and the client reconnects.
    let mut ping = tokio::time::interval(Duration::from_secs(15));
    ping.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    ping.tick().await; // the first tick is immediate; skip it
    loop {
        tokio::select! {
            msg = rx.recv() => match msg {
                Ok(body) => {
                    if socket.send(Message::Text(body.into())).await.is_err() {
                        break;
                    }
                }
                Err(broadcast::error::RecvError::Lagged(_)) => continue,
                Err(_) => break,
            },
            _ = ping.tick() => {
                if socket.send(Message::Ping(Vec::new().into())).await.is_err() {
                    break;
                }
            }
            incoming = socket.recv() => {
                // Read-only dashboard: client messages are ignored, but a
                // closed socket must end the task.
                match incoming {
                    None | Some(Err(_)) | Some(Ok(Message::Close(_))) => break,
                    Some(Ok(_)) => {}
                }
            }
        }
    }
}

fn arg(flag: &str, default: &str) -> String {
    let a: Vec<String> = std::env::args().collect();
    a.iter()
        .position(|x| x == flag)
        .and_then(|i| a.get(i + 1))
        .cloned()
        .unwrap_or_else(|| default.to_string())
}

#[tokio::main]
async fn main() {
    let port: u16 = arg("--port", "8787").parse().unwrap_or_else(|_| {
        eprintln!("--port must be an integer from 1 to 65535");
        std::process::exit(2);
    });
    if port == 0 {
        eprintln!("--port must be an integer from 1 to 65535");
        std::process::exit(2);
    }

    // Run from the repo root or from dashboard/; both resolve to the repo.
    let mut root = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    if root.file_name().map(|n| n == "dashboard").unwrap_or(false) {
        root = root.parent().map(Path::to_path_buf).unwrap_or(root);
    }
    if let Ok(p) = std::env::var("ZTT_ROOT") {
        root = PathBuf::from(p);
    }

    let (tx, _) = broadcast::channel::<String>(32);
    let mut series = collector::Collector::new();
    let state = AppState {
        tx,
        current: Arc::new(Mutex::new(
            build(&root, &mut series, Value::Null).to_string(),
        )),
        root: root.clone(),
        port,
        series: Arc::new(Mutex::new(series)),
        last_scan: Arc::new(std::sync::Mutex::new(None)),
        scan_running: Arc::new(std::sync::atomic::AtomicBool::new(false)),
        last_run: Arc::new(std::sync::Mutex::new(None)),
        clients: Arc::new(std::sync::atomic::AtomicUsize::new(0)),
    };
    tokio::spawn(watch(state.clone()));

    let app = router(state);

    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    println!("zero-trust dashboard  http://localhost:{port}");
    println!("watching              {}", root.display());
    // Exit non-zero: Task Scheduler records Last Result, and a monitor that
    // could not bind used to look exactly like a healthy start.
    let listener = match tokio::net::TcpListener::bind(addr).await {
        Ok(l) => l,
        Err(e) => {
            eprintln!("port {port} unavailable: {e}");
            std::process::exit(1);
        }
    };
    if let Err(e) = axum::serve(listener, app).await {
        eprintln!("dashboard server failed: {e}");
        std::process::exit(1);
    }
}

/// Additive fields do not bump this; a field that changes meaning or disappears does.
const SCHEMA_VERSION: u32 = 1;

/// What a client can ask before it trusts anything else: the protocol, this
/// process, and the capabilities it is allowed to assume.
async fn meta(State(st): State<AppState>) -> Response {
    Json(json!({
        "schema_version": SCHEMA_VERSION,
        "product": "Zero-Trust Console",
        "mode": "monitor",
        "writes": ["POST /api/scan"],
        "port": st.port,
        "root_label": "the tree the monitor was launched in",
        "capabilities": {
            "scan": true,
            "logs": true,
            "report": true,
            "architecture": true,
            "execution": false,
            "approval": false,
            "sizing": false,
        },
    }))
    .into_response()
}

fn router(state: AppState) -> Router {
    Router::new()
        .route("/", get(index))
        .route("/architecture", get(architecture))
        .route("/api/snapshot", get(snapshot))
        .route("/api/meta", get(meta))
        .route("/api/scan", post(trigger_scan))
        .route("/api/logs/{which}", get(full_log))
        .route("/api/report/latest", get(latest_report))
        .route("/ws", get(ws))
        .route("/vendor/react.production.min.js", get(|| js("react")))
        .route(
            "/vendor/react-dom.production.min.js",
            get(|| js("react-dom")),
        )
        .route("/vendor/htm.umd.js", get(|| js("htm")))
        // Charts. Pinned tarball, sha256 in vendor/PROVENANCE.json, never a CDN.
        .route(
            "/vendor/echarts.min.js",
            get(|| async {
                (
                    [(
                        header::CONTENT_TYPE,
                        "application/javascript; charset=utf-8",
                    )],
                    include_str!("../web/vendor/echarts.min.js"),
                )
            }),
        )
        .layer(middleware::from_fn_with_state(state.clone(), local_request))
        .with_state(state)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Two tests spawn a child and `ZTT_PYTHON` is process-wide, so each holds
    /// this across its set..remove window and cannot see the other's interpreter.
    static SPAWN_ENV: std::sync::Mutex<()> = std::sync::Mutex::new(());

    fn tmp(name: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!("ztt-{}-{}", std::process::id(), name));
        let _ = std::fs::remove_file(&p);
        p
    }

    #[test]
    fn a_stage_artifact_is_projected_not_shipped_whole() {
        let wide = serde_json::json!({
            "scan_id": "abc", "total_tests": 741,
            "all_hits": (0..100).map(|i| serde_json::json!({"i": i})).collect::<Vec<_>>(),
            "payload": "x".repeat(1000)
        });
        let out = trim_stage(
            &wide,
            &["scan_id", "total_tests", "all_hits"],
            &["all_hits"],
            20,
        );
        assert_eq!(out["scan_id"], "abc");
        assert_eq!(out["total_tests"], 741);
        assert_eq!(
            out.as_object().unwrap().len(),
            3,
            "an unlisted field survived"
        );
        let hits = out["all_hits"].as_array().unwrap();
        assert_eq!(hits.len(), 20);
        // the cap keeps the newest, which are the last rows of the document
        assert_eq!(hits[19]["i"], 99);
    }

    #[test]
    fn a_missing_stage_artifact_stays_null() {
        let out = trim_stage(&serde_json::Value::Null, &["scan_id"], &[], 20);
        assert!(out.is_null(), "a missing file became an empty object");
    }

    #[test]
    fn tail_reads_the_end_of_a_file_larger_than_its_window() {
        let p = tmp("tail.log");
        let mut body = String::new();
        for i in 0..2000 {
            body.push_str(&format!("line {i} {}\n", "x".repeat(40)));
        }
        std::fs::write(&p, &body).unwrap(); // ~100 KiB, well past 16 KiB
        let got = tail(&p, 3).expect("file exists");
        assert_eq!(got.len(), 3);
        assert!(got[2].starts_with("line 1999"), "{got:?}");
        std::fs::remove_file(&p).ok();
    }

    #[test]
    fn a_missing_file_is_none_and_an_empty_one_is_some_empty() {
        let missing = tmp("missing.log");
        assert!(tail(&missing, 3).is_none());

        let empty = tmp("empty.log");
        std::fs::write(&empty, "").unwrap();
        assert_eq!(tail(&empty, 3), Some(vec![]));
        std::fs::remove_file(&empty).ok();
    }

    #[test]
    fn log_excerpt_is_bounded_and_keeps_the_newest_complete_lines() {
        let p = tmp("excerpt.log");
        std::fs::write(&p, format!("{}\nnewest é\n", "old ".repeat(100))).unwrap();
        let (text, truncated, total) = log_excerpt(&p, 32).unwrap();
        assert!(truncated && total > 32);
        assert!(text.len() <= 32 && text.ends_with("newest é\n"), "{text:?}");
        let (whole, truncated, _) = log_excerpt(&p, 1000).unwrap();
        assert!(!truncated && whole.starts_with("old "));
        std::fs::remove_file(p).unwrap();
    }

    #[test]
    fn json_reads_refuse_oversized_and_invalid_data() {
        let p = tmp("bounded.json");
        std::fs::write(&p, b"123456").unwrap();
        assert!(read_bounded(&p, 5).is_err());
        std::fs::write(&p, b"\xff").unwrap();
        assert!(read_bounded(&p, 5).is_err());
        assert!(read_json(&p).is_null());
        std::fs::remove_file(p).unwrap();
    }

    #[test]
    fn engine_display_drops_identifiers_and_unknown_fields() {
        let display = display_engine(json!({"ok":true, "applied":false, "private":"hidden",
            "account":{"login":123456789, "equity":10, "server":"fixture-demo", "name":"hidden"},
            "positions":[{"ticket":42, "symbol":"FIXTURE", "private":"hidden"}]}));
        let text = display.to_string();
        assert!(!text.contains("123456789") && !text.contains("hidden"));
        assert_eq!(display["account"]["label"], "account •••789");
        assert!(display["positions"][0].get("ticket").is_none());
        let failed = display_engine(json!({"ok":false, "positions":[]}));
        assert!(failed["positions"].is_null());
        let malformed = display_engine(json!({"ok":true, "positions":[null]}));
        assert_eq!(malformed["ok"], false);
        assert!(malformed["positions"].is_null());
        let missing_ok = display_engine(json!({"positions": []}));
        assert_eq!(missing_ok["ok"], false);
        assert!(missing_ok["positions"].is_null());
        assert!(missing_ok["error"]
            .as_str()
            .unwrap_or("")
            .contains("missing ok"));
        assert!(display_engine(Value::Null).is_null());
    }

    #[test]
    fn a_zero_exit_without_a_ledger_row_is_a_noop() {
        assert_eq!(scan_outcome(Some(0), 100, 100), "noop");
        assert_eq!(scan_outcome(Some(0), 100, 180), "completed");
        assert_eq!(scan_outcome(Some(2), 100, 180), "failed");
        assert_eq!(scan_outcome(None, 100, 100), "failed");
    }

    #[test]
    fn local_header_policy_rejects_foreign_missing_and_ambiguous_authorities() {
        let mut headers = HeaderMap::new();
        headers.insert(header::HOST, "localhost:8787".parse().unwrap());
        assert!(local_headers(&headers, 8787, false));
        assert!(!local_headers(&headers, 8787, true));
        headers.insert(header::ORIGIN, "http://127.0.0.1:8787".parse().unwrap());
        assert!(local_headers(&headers, 8787, true));
        for origin in [
            "null",
            "http://foreign.invalid:8787",
            "http://localhost:8788",
            "https://localhost:8787",
        ] {
            headers.insert(header::ORIGIN, origin.parse().unwrap());
            assert!(!local_headers(&headers, 8787, true), "{origin}");
        }
        headers.insert(header::ORIGIN, "http://localhost:8787".parse().unwrap());
        headers.insert(header::HOST, "foreign.invalid:8787".parse().unwrap());
        assert!(!local_headers(&headers, 8787, false));
        headers.insert(header::HOST, "localhost:8787".parse().unwrap());
        headers.append(header::HOST, "localhost:8787".parse().unwrap());
        assert!(!local_headers(&headers, 8787, false));
    }

    fn fixture_dir(name: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!(
            "ztt-{}-{name}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir(&p).unwrap();
        p
    }

    #[test]
    fn current_scan_requires_fresh_matching_artifacts_and_the_latest_completed_row() {
        let root = fixture_dir("scan");
        for name in ["tier2_candidates.json", "tier3_results.json"] {
            std::fs::write(root.join(name), "{}").unwrap();
        }
        let t2 = json!({"scan_id":"fixture"});
        let t3 = json!({"scan_id":"fixture"});
        // The row the writer actually emits for a winning scan: "WINNER:SYM,...".
        let row = "2026-11-09T12:00:00Z | scan_id=fixture | WINNER:EURUSD,GBPUSD".to_string();
        assert_eq!(
            scan_state(&root, &t2, &t3, Some(&vec![row.clone()]))["current"],
            true
        );
        assert_eq!(
            scan_state(
                &root,
                &t2,
                &json!({"scan_id":"other"}),
                Some(&vec![row.clone()])
            )["current"],
            false
        );
        assert_eq!(
            scan_state(
                &root,
                &t2,
                &t3,
                Some(&vec![
                    row.clone(),
                    "later | scan_id=newer | FAILED preflight".into()
                ])
            )["current"],
            false
        );
        assert_eq!(
            scan_state(&root, &json!({}), &json!({}), Some(&vec![row.clone()]))["current"],
            false
        );
        let stale = SystemTime::now() - Duration::from_secs(SCAN_MAX_AGE + 10);
        std::fs::File::options()
            .write(true)
            .open(root.join("tier3_results.json"))
            .unwrap()
            .set_times(std::fs::FileTimes::new().set_modified(stale))
            .unwrap();
        assert_eq!(
            scan_state(&root, &t2, &t3, Some(&vec![row]))["current"],
            false
        );
        assert_eq!(root.parent(), Some(std::env::temp_dir().as_path()));
        std::fs::remove_dir_all(root).unwrap();
    }

    async fn http_request(port: u16, path: &str, headers: String) -> String {
        let path = path.to_string();
        tokio::task::spawn_blocking(move || {
            use std::io::Write;
            let mut stream = std::net::TcpStream::connect(("127.0.0.1", port)).unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            write!(
                stream,
                "GET {path} HTTP/1.1\r\n{headers}Connection: close\r\n\r\n"
            )
            .unwrap();
            let mut response = String::new();
            stream.read_to_string(&mut response).unwrap();
            response
        })
        .await
        .unwrap()
    }

    /// Start the real router on an ephemeral port against `root`.
    async fn serve(root: &Path) -> (u16, AppState) {
        let listener = tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0))
            .await
            .unwrap();
        let port = listener.local_addr().unwrap().port();
        let (tx, _) = broadcast::channel(2);
        let mut series = collector::Collector::new();
        let st = AppState {
            tx,
            current: Arc::new(Mutex::new(
                build(root, &mut series, Value::Null).to_string(),
            )),
            root: root.to_path_buf(),
            port,
            series: Arc::new(Mutex::new(series)),
            last_scan: Arc::new(std::sync::Mutex::new(None)),
            scan_running: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            last_run: Arc::new(std::sync::Mutex::new(None)),
            clients: Arc::new(std::sync::atomic::AtomicUsize::new(0)),
        };
        let state = st.clone();
        tokio::spawn(async move {
            axum::serve(listener, router(st)).await.unwrap();
        });
        (port, state)
    }

    fn headers(port: u16) -> String {
        format!("Host: localhost:{port}\r\nOrigin: http://localhost:{port}\r\n")
    }

    #[tokio::test]
    async fn a_route_that_sets_its_own_csp_keeps_it() {
        // D1, the fix that had no test: the middleware inserted its default on
        // every response, so the report route's sandbox was overwritten on every
        // request and the backstop the comment argued for never ran.
        let root = fixture_dir("csp");
        std::fs::write(root.join("reports.tmp"), "").unwrap();
        let report_dir = root.join("reports");
        std::fs::create_dir(&report_dir).unwrap();
        std::fs::write(
            report_dir.join("scan_1.html"),
            "<!doctype html><p>report</p>",
        )
        .unwrap();
        let (port, _st) = serve(&root).await;

        let report = http_request(port, "/api/report/latest", headers(port)).await;
        assert!(
            report.to_ascii_lowercase().contains("sandbox")
                && report.contains("default-src 'none'"),
            "the report kept its own policy: {report}"
        );
        assert!(report.contains("frame-ancestors 'none'"), "{report}");

        // and the default still fills in where nothing was declared
        let ui = http_request(port, "/", headers(port)).await;
        assert!(ui.contains("frame-ancestors 'none'"), "{ui}");
        assert!(!ui.to_ascii_lowercase().contains("sandbox;"), "{ui}");
    }

    #[tokio::test]
    async fn the_monitor_reports_the_kill_switch_and_never_sets_it() {
        // One marker, two readers: position_manager refuses on it and run_funnel
        // refuses on it; this process only says so. It must never be able to arm
        // or disarm the system itself - a monitor with a switch is a control
        // surface, and this process has exactly one write already.
        let root = fixture_dir("kill");
        let mut series = collector::Collector::new();
        let before = build(&root, &mut series, Value::Null);
        assert!(before["kill_switch"].is_null(), "{before}");
        assert!(!root.join("KILL_SWITCH").exists(), "nothing was written");

        std::fs::write(root.join("KILL_SWITCH"), "").unwrap();
        let after = build(&root, &mut series, Value::Null);
        assert_eq!(
            after["kill_switch"],
            json!("KILL_SWITCH exists at the tree root"),
            "{after}"
        );
        std::fs::remove_file(root.join("KILL_SWITCH")).unwrap();
        let cleared = build(&root, &mut series, Value::Null);
        assert!(
            cleared["kill_switch"].is_null(),
            "clearing it clears the field"
        );
    }

    #[tokio::test]
    async fn the_client_can_ask_what_this_process_is_allowed_to_do() {
        // /api/meta is the one place a client learns the contract and the boundary:
        // a schema version it can refuse, and four false capabilities it must not
        // assume. If this ever reports execution: true, the redesign went wrong.
        let root = fixture_dir("meta");
        let (port, _st) = serve(&root).await;
        let body = http_request(port, "/api/meta", headers(port)).await;
        assert!(body.starts_with("HTTP/1.1 200"), "{body}");
        assert!(body.contains("\"schema_version\":1"), "{body}");
        assert!(body.contains("\"execution\":false"), "{body}");
        assert!(body.contains("\"approval\":false"), "{body}");
        assert!(
            body.contains("POST /api/scan"),
            "the only write is named: {body}"
        );
    }

    #[tokio::test]
    async fn a_scan_that_runs_is_refused_twice_and_its_exit_code_is_recorded() {
        // D2, end to end and in CI: the exit code of the child - the only honest
        // "it ran" - travels from the wait thread into last_scan_run, and a second
        // POST while it lives is refused.
        let root = fixture_dir("scan");
        std::fs::write(
            root.join("run_funnel.py"),
            "import time\ntime.sleep(1.0)\nraise SystemExit(7)\n",
        )
        .unwrap();
        // The interpreter the launcher would pick is not on every runner (the
        // Windows image has no `py -3`), so the test names one explicitly. This is
        // the only test that spawns, and it is the only reader of the variable.
        // A real interpreter, not the Windows Store alias: that one answers
        // `--version` and then does nothing when asked to run a script.
        let Some(interp) = ["python3", "python", "py"].into_iter().find(|p| {
            std::process::Command::new(p)
                .args(["-c", "print('ok')"])
                .output()
                .map(|o| o.status.success() && String::from_utf8_lossy(&o.stdout).trim() == "ok")
                .unwrap_or(false)
        }) else {
            eprintln!("no usable interpreter here; the lifecycle is covered on Linux CI");
            return;
        };
        let _env = SPAWN_ENV.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var("ZTT_PYTHON", interp);

        let (port, st) = serve(&root).await;
        let first = http_request(port, "/", headers(port)).await; // warm the router
        assert!(first.starts_with("HTTP/1.1 200"));

        let res = trigger_scan(axum::extract::State(st.clone())).await;
        let status = res.status();
        let body = axum::body::to_bytes(res.into_body(), 4096)
            .await
            .unwrap_or_default();
        if status == StatusCode::INTERNAL_SERVER_ERROR {
            // The runner has no interpreter this process can spawn. The refusal
            // must still be honest and journalled - the other half of the same
            // state machine, and the branch a user hits on a broken venv.
            let journal = std::fs::read_to_string(root.join("dashboard_scan.jsonl")).unwrap();
            assert!(journal.contains("\"outcome\":500"), "{journal}");
            std::env::remove_var("ZTT_PYTHON");
            return;
        }
        assert_eq!(
            status,
            StatusCode::ACCEPTED,
            "the first POST starts one: {}",
            String::from_utf8_lossy(&body)
        );

        let second = trigger_scan(axum::extract::State(st.clone())).await;
        let status = second.status();
        let body = axum::body::to_bytes(second.into_body(), 4096)
            .await
            .unwrap_or_default();
        assert_eq!(
            status,
            StatusCode::CONFLICT,
            "a live child refuses a second"
        );
        assert!(
            String::from_utf8_lossy(&body).contains("scan_running"),
            "the refusal names itself: {}",
            String::from_utf8_lossy(&body)
        );

        for _ in 0..60 {
            tokio::time::sleep(Duration::from_millis(200)).await;
            if !st.scan_running.load(std::sync::atomic::Ordering::SeqCst) {
                break;
            }
        }
        std::env::remove_var("ZTT_PYTHON");
        let run = st.last_run.lock().unwrap().clone().unwrap();
        assert_eq!(
            run["exit"],
            json!(7),
            "the child's exit code is recorded: {run}"
        );
        assert_eq!(run["running"], json!(false), "{run}");
        // and the journal survived, because the snapshot is memory only
        let journal = std::fs::read_to_string(root.join("dashboard_scan.jsonl")).unwrap();
        assert!(journal.contains("\"outcome\":409"), "{journal}");
        assert!(journal.contains("\"exit\":7"), "{journal}");
    }

    #[tokio::test]
    async fn two_concurrent_posts_start_exactly_one_scan() {
        // SECURITY-03: the running check and the running set were two steps, so
        // two requests arriving together both passed the check and both spawned
        // an interpreter. The claim is atomic now; the loser is refused.
        let root = fixture_dir("scan-race");
        std::fs::write(
            root.join("run_funnel.py"),
            "import time\ntime.sleep(1.0)\n",
        )
        .unwrap();
        let Some(interp) = ["python3", "python", "py"].into_iter().find(|p| {
            std::process::Command::new(p)
                .args(["-c", "print('ok')"])
                .output()
                .map(|o| o.status.success() && String::from_utf8_lossy(&o.stdout).trim() == "ok")
                .unwrap_or(false)
        }) else {
            eprintln!("no usable interpreter here; the race is covered on Linux CI");
            return;
        };
        let _env = SPAWN_ENV.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var("ZTT_PYTHON", interp);

        let (_port, st) = serve(&root).await;
        let (first, second) = tokio::join!(
            trigger_scan(axum::extract::State(st.clone())),
            trigger_scan(axum::extract::State(st.clone()))
        );
        let statuses = [first.status(), second.status()];
        let accepted = statuses
            .iter()
            .filter(|s| **s == StatusCode::ACCEPTED)
            .count();
        assert_eq!(accepted, 1, "one claim, one child: {statuses:?}");
        let refused = statuses
            .iter()
            .find(|s| **s != StatusCode::ACCEPTED)
            .expect("the loser has a status");
        assert!(
            *refused == StatusCode::CONFLICT || *refused == StatusCode::TOO_MANY_REQUESTS,
            "the loser is refused, not started: {statuses:?}"
        );

        for _ in 0..60 {
            tokio::time::sleep(Duration::from_millis(200)).await;
            if !st.scan_running.load(std::sync::atomic::Ordering::SeqCst) {
                break;
            }
        }
        std::env::remove_var("ZTT_PYTHON");
    }

    #[tokio::test]
    async fn the_seventeenth_socket_is_refused_and_a_closed_one_frees_its_slot() {
        let root = fixture_dir("ws");
        let (port, st) = serve(&root).await;
        // Blocking sockets, so they run on a blocking thread: a read inside the
        // async test body would stall the reactor that has to answer it.
        let (sockets, refused) = tokio::task::spawn_blocking(move || {
            use std::io::{Read, Write};
            let base = format!(
                "GET /ws HTTP/1.1\r\nHost: localhost:{port}\r\nOrigin: http://localhost:{port}\r\n\
                 Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\n\
                 Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
            );
            let mut sockets = Vec::new();
            for _ in 0..16 {
                let mut s = std::net::TcpStream::connect(("127.0.0.1", port)).unwrap();
                s.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
                s.write_all(base.as_bytes()).unwrap();
                let mut buf = [0u8; 64];
                let n = s.read(&mut buf).unwrap();
                let head = String::from_utf8_lossy(&buf[..n]).to_string();
                // The 101 can arrive in two segments; the status line is enough
                // to prove the upgrade, and reading the rest would block.
                assert!(head.starts_with("HTTP/1.1 101"), "{head}");
                sockets.push(s);
            }
            let mut probe = std::net::TcpStream::connect(("127.0.0.1", port)).unwrap();
            probe
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            write!(
                probe,
                "GET /ws HTTP/1.1\r\nHost: localhost:{port}\r\nOrigin: http://localhost:{port}\r\n\
                 Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\n\
                 Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
            )
            .unwrap();
            let mut out = String::new();
            let _ = probe.read_to_string(&mut out);
            (sockets, out)
        })
        .await
        .unwrap();

        assert!(
            refused.starts_with("HTTP/1.1 503") && refused.contains("too many dashboard clients"),
            "{refused}"
        );
        drop(sockets);
        for _ in 0..60 {
            if st.clients.load(std::sync::atomic::Ordering::SeqCst) == 0 {
                break;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        assert_eq!(
            st.clients.load(std::sync::atomic::Ordering::SeqCst),
            0,
            "every closed socket gave its slot back"
        );
    }

    #[tokio::test]
    async fn http_routes_enforce_host_and_origin_before_exposing_fixture_state() {
        let root = fixture_dir("http");
        std::fs::write(
            root.join("engine_state.json"),
            r#"{"ok":true,"account":{"login":123456789,"equity":10},"positions":[]}"#,
        )
        .unwrap();
        std::fs::write(root.join("funnel_history.log"), "fixture\n").unwrap();
        std::fs::write(root.join("audit.jsonl"), "").unwrap();
        let listener = tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0))
            .await
            .unwrap();
        let port = listener.local_addr().unwrap().port();
        let (tx, _) = broadcast::channel(2);
        let mut series = collector::Collector::new();
        let st = AppState {
            tx,
            current: Arc::new(Mutex::new(
                build(&root, &mut series, Value::Null).to_string(),
            )),
            root: root.clone(),
            port,
            series: Arc::new(Mutex::new(series)),
            last_scan: Arc::new(std::sync::Mutex::new(None)),
            scan_running: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            last_run: Arc::new(std::sync::Mutex::new(None)),
            clients: Arc::new(std::sync::atomic::AtomicUsize::new(0)),
        };
        let server = tokio::spawn(async move {
            axum::serve(listener, router(st)).await.unwrap();
        });
        let allowed = format!("Host: localhost:{port}\r\nOrigin: http://localhost:{port}\r\n");
        let response = http_request(port, "/api/snapshot", allowed.clone()).await;
        assert!(response.starts_with("HTTP/1.1 200"));
        assert!(response.contains("no-store") && !response.contains("123456789"));
        let denied = http_request(
            port,
            "/api/snapshot",
            format!("Host: foreign.invalid:{port}\r\n"),
        )
        .await;
        assert!(denied.starts_with("HTTP/1.1 403") && !denied.contains("equity"));
        let denied = http_request(
            port,
            "/ws",
            format!("Host: localhost:{port}\r\nOrigin: http://foreign.invalid\r\n"),
        )
        .await;
        assert!(denied.starts_with("HTTP/1.1 403"));
        let log = http_request(port, "/api/logs/funnel", allowed.clone()).await;
        assert!(log.starts_with("HTTP/1.1 200") && log.contains("x-log-truncated: false"));
        assert!(http_request(port, "/api/logs/unknown", allowed)
            .await
            .starts_with("HTTP/1.1 404"));
        server.abort();
        let _ = server.await;
        assert_eq!(root.parent(), Some(std::env::temp_dir().as_path()));
        std::fs::remove_dir_all(root).unwrap();
    }
}
