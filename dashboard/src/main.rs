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
//!   GET /ws            updates on change, plus a 1 Hz heartbeat so engine_age_s
//!                      ticks in the browser instead of freezing between writes

use axum::{
    extract::{
        ws::{Message, WebSocket, WebSocketUpgrade},
        Path as AxumPath, State,
    },
    http::StatusCode,
    response::{Html, IntoResponse},
    routing::get,
    Json, Router,
};
use serde_json::{json, Value};
use std::{
    io::{Read, Seek, SeekFrom},
    net::SocketAddr,
    path::{Path, PathBuf},
    sync::Arc,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::sync::{broadcast, Mutex};

const TAIL: usize = 12;

#[derive(Clone)]
struct AppState {
    tx: broadcast::Sender<String>,
    current: Arc<Mutex<String>>,
    root: PathBuf,
}

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
    f.read_to_end(&mut bytes).ok()?;
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
    SystemTime::now().duration_since(m).ok().map(|d| d.as_secs())
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

/// Null when the file is absent or mid-write: the snapshot must never fail
/// because a producer was between `open()` and `write()`.
fn read_json(path: &Path) -> Value {
    std::fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or(Value::Null)
}

fn build(root: &Path) -> Value {
    let engine_path = newest_state(root);
    let engine: Value = std::fs::read_to_string(&engine_path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or(Value::Null);

    // None, not 0: no engine state means we do not know the position count.
    // Reporting 0 there is the difference between "flat" and "blind".
    let positions = engine
        .get("positions")
        .and_then(Value::as_array)
        .map(|v| v.len());
    let equity = engine.get("account").and_then(|a| a.get("equity")).cloned();
    let balance = engine.get("account").and_then(|a| a.get("balance")).cloned();
    let currency = engine
        .get("account")
        .and_then(|a| a.get("currency"))
        .cloned()
        .unwrap_or(json!(""));
    let server = engine.get("account").and_then(|a| a.get("server")).cloned();

    let mcp = resolve_audit(root).and_then(|p| tail(&p, TAIL));

    // The two stage artifacts the funnel leaves behind. Absent outside a scan
    // window, which is the normal state most of the day.
    let tier2 = read_json(&root.join("tier2_candidates.json"));
    let tier3 = read_json(&root.join("tier3_results.json"));

    json!({
        "ts": SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0),
        "engine": engine,
        "engine_age_s": age_secs(&engine_path),
        "tier2": tier2,
        "tier3": tier3,
        "equity": equity,
        "balance": balance,
        "currency": currency,
        "server": server,
        "positions": positions,
        "funnel": tail(&root.join("funnel_history.log"), TAIL),
        "actions": tail(&root.join("position_manager.log"), TAIL),
        "mcp": mcp,
    })
}

async fn watch(state: AppState) {
    let mut last = String::new();
    let mut tick = tokio::time::interval(Duration::from_secs(1));
    loop {
        tick.tick().await;
        let body = serde_json::to_string(&build(&state.root)).unwrap_or_default();
        if body != last {
            last = body.clone();
            *state.current.lock().await = body.clone();
            let _ = state.tx.send(body);
        }
    }
}

/// Truncate to `max` bytes on a UTF-8 boundary. `chars().take(max)` walked and
/// copied the whole string; `truncate` drops the tail in place.
fn clip(mut text: String, max: usize) -> String {
    if text.len() > max {
        let end = (0..=max).rev().find(|i| text.is_char_boundary(*i)).unwrap_or(0);
        text.truncate(end);
    }
    text
}

async fn latest_report(State(st): State<AppState>) -> impl IntoResponse {
    // The per-scan HTML report is the deep view; this is the bridge to it from
    // the live view. Newest file wins -- one scan, one page.
    let dir = st.root.join("reports");
    let mut newest: Option<(std::time::SystemTime, std::path::PathBuf)> = None;
    if let Ok(entries) = std::fs::read_dir(&dir) {
        for e in entries.flatten() {
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
    }
    match newest {
        Some((_, path)) => {
            let body = std::fs::read_to_string(&path).unwrap_or_default();
            (
                StatusCode::OK,
                [(axum::http::header::CONTENT_TYPE, "text/html; charset=utf-8")],
                body,
            )
        }
        None => (
            StatusCode::NOT_FOUND,
            [(axum::http::header::CONTENT_TYPE, "text/plain")],
            String::from("no reports yet - the next scan writes one"),
        ),
    }
}

async fn full_log(AxumPath(which): AxumPath<String>, State(st): State<AppState>) -> impl IntoResponse {
    // The snapshot only carries the last 12 lines: enough to glance at, useless
    // for debugging. This serves the whole file so the Copy button copies
    // everything. Loopback-only listener, so no new exposure.
    let path = match which.as_str() {
        "funnel" => Some(st.root.join("funnel_history.log")),
        "actions" => Some(st.root.join("position_manager.log")),
        "audit" => resolve_audit(&st.root),
        _ => return (StatusCode::NOT_FOUND, String::from("unknown log")),
    };
    // A missing file must not answer 200 with "": the Copy button would flash
    // "copied" and hand the operator an empty clipboard.
    //
    // This is the only read that can be large, so it leaves the reactor:
    // spawn_blocking keeps a 4 MB copy from stalling the WebSocket heartbeat.
    let Some(path) = path else {
        return (StatusCode::NOT_FOUND, String::from("log file not found"));
    };
    let read = tokio::task::spawn_blocking(move || std::fs::read_to_string(path).ok())
        .await
        .unwrap_or(None);
    match read {
        // ponytail: whole file up to 4 MB. Stream it if a log ever outgrows memory.
        Some(text) => (StatusCode::OK, clip(text, 4_000_000)),
        None => (StatusCode::NOT_FOUND, String::from("log file not found")),
    }
}

/// The Archify diagram of this pipeline, committed as a static artifact.
/// Embedded rather than read from disk: it is a build input, and a monitor that
/// serves a missing diagram as a blank page would be the same lie as a zero.
async fn architecture() -> Html<&'static str> {
    Html(include_str!("../web/architecture.html"))
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
    ([(axum::http::header::CONTENT_TYPE, "application/javascript")], body)
}

async fn snapshot(State(st): State<AppState>) -> impl IntoResponse {
    Json(build(&st.root))
}

async fn ws(ws: WebSocketUpgrade, State(st): State<AppState>) -> impl IntoResponse {
    ws.on_upgrade(move |socket| client(socket, st))
}

async fn client(mut socket: WebSocket, st: AppState) {
    let mut rx = st.tx.subscribe();
    let first = st.current.lock().await.clone();
    if socket.send(Message::Text(first)).await.is_err() {
        return;
    }
    loop {
        tokio::select! {
            msg = rx.recv() => match msg {
                Ok(body) => {
                    if socket.send(Message::Text(body)).await.is_err() {
                        break;
                    }
                }
                Err(broadcast::error::RecvError::Lagged(_)) => continue,
                Err(_) => break,
            },
            incoming = socket.recv() => {
                // Read-only dashboard: client messages are ignored, but a
                // closed socket must end the task.
                match incoming {
                    None | Some(Err(_)) => break,
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
    let port: u16 = arg("--port", "8787").parse().unwrap_or(8787);

    // Run from the repo root or from dashboard/; both resolve to the repo.
    let mut root = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    if root.file_name().map(|n| n == "dashboard").unwrap_or(false) {
        root = root.parent().map(Path::to_path_buf).unwrap_or(root);
    }
    if let Ok(p) = std::env::var("ZTT_ROOT") {
        root = PathBuf::from(p);
    }

    let (tx, _) = broadcast::channel::<String>(32);
    let state = AppState {
        tx,
        current: Arc::new(Mutex::new(String::from("{}"))),
        root: root.clone(),
    };
    tokio::spawn(watch(state.clone()));

    let app = Router::new()
        .route("/", get(index))
        .route("/architecture", get(architecture))
        .route("/api/snapshot", get(snapshot))
        .route("/api/logs/:which", get(full_log))
        .route("/api/report/latest", get(latest_report))
        .route("/ws", get(ws))
        .route("/vendor/react.production.min.js", get(|| js("react")))
        .route("/vendor/react-dom.production.min.js", get(|| js("react-dom")))
        .route("/vendor/htm.umd.js", get(|| js("htm")))
        .with_state(state);

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
    let _ = axum::serve(listener, app).await;
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!("ztt-{}-{}", std::process::id(), name));
        let _ = std::fs::remove_file(&p);
        p
    }

    #[test]
    fn tail_reads_the_end_of_a_file_larger_than_its_window() {
        let p = tmp("tail.log");
        let mut body = String::new();
        for i in 0..2000 {
            body.push_str(&format!("line {i} {}\n", "x".repeat(40)));
        }
        std::fs::write(&p, &body).unwrap();          // ~100 KiB, well past 16 KiB
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
    fn clip_never_splits_a_character() {
        assert_eq!(clip("abcdef".to_string(), 3), "abc");
        let wide: String = std::iter::repeat('\u{e9}').take(10).collect(); // 2 bytes each
        let out = clip(wide, 5);                        // 5 would split the third char
        assert!(out.len() <= 5 && out.chars().all(|c| c == '\u{e9}'), "{out:?}");
    }
}
