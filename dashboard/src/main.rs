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
    process::Command,
    net::SocketAddr,
    path::{Path, PathBuf},
    sync::Arc,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::sync::{broadcast, Mutex};

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

/// Null when the file is absent or mid-write: the snapshot must never fail
/// because a producer was between `open()` and `write()`.
fn read_json(path: &Path) -> Value {
    read_bounded(path, JSON_LIMIT)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or(Value::Null)
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
    let positions_valid = object.get("positions").and_then(Value::as_array)
        .is_some_and(|items| items.iter().all(Value::is_object));
    if object.get("ok").and_then(Value::as_bool) == Some(true) && !positions_valid {
        object.insert("ok".into(), json!(false));
        object.insert("error".into(), json!("invalid position observation"));
    }
    if object.get("ok").and_then(Value::as_bool) != Some(true) {
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
                    [
                        "symbol", "side", "volume", "entry", "price", "sl", "profit",
                    ]
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
    let verdict = row
        .split(" | ")
        .nth(2)
        .and_then(|s| s.split_whitespace().next());
    let ids_match = t2_id.is_some() && t2_id == t3_id && t2_id == ledger_id;
    let fresh = ["tier2_candidates.json", "tier3_results.json"]
        .iter()
        .all(|name| age_secs(&root.join(name)).is_some_and(|age| age <= SCAN_MAX_AGE));
    let current = ids_match && fresh && matches!(verdict, Some("STAND_DOWN" | "PROPOSAL"));
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

fn build(root: &Path) -> Value {
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
    let tier2 = read_json(&root.join("tier2_candidates.json"));
    let tier3 = read_json(&root.join("tier3_results.json"));
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

    json!({
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
    })
}

async fn watch(state: AppState) {
    let mut last = String::new();
    let mut tick = tokio::time::interval(Duration::from_secs(1));
    loop {
        tick.tick().await;
        let root = state.root.clone();
        let body =
            match tokio::task::spawn_blocking(move || serde_json::to_string(&build(&root))).await {
                Ok(Ok(body)) => body,
                _ => {
                    eprintln!("dashboard snapshot watcher failed");
                    return;
                }
            };
        if body != last {
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
            [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
            body,
        ),
        Ok(Ok(None)) => (
            StatusCode::NOT_FOUND,
            [(header::CONTENT_TYPE, "text/plain; charset=utf-8")],
            String::from("no reports yet - the next scan writes one"),
        ),
        _ => (
            StatusCode::SERVICE_UNAVAILABLE,
            [(header::CONTENT_TYPE, "text/plain; charset=utf-8")],
            String::from("latest report is unreadable or exceeds the 8 MB limit"),
        ),
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
    let mut response = next.run(request).await;
    for (name, value) in [
        (header::CACHE_CONTROL, "no-store"),
        (header::X_CONTENT_TYPE_OPTIONS, "nosniff"),
        (header::CONTENT_SECURITY_POLICY, "frame-ancestors 'none'"),
    ] {
        response.headers_mut().insert(name, value.parse().unwrap());
    }
    response
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
    let override_python = std::env::var("ZTT_PYTHON").ok();
    let mut interpreter = Command::new(override_python.clone().unwrap_or_else(|| {
        if cfg!(windows) { "py".to_string() } else { "python3".to_string() }
    }));
    if override_python.is_none() && cfg!(windows) {
        interpreter.arg("-3");
    }
    let script = st.root.join("run_funnel.py");
    if !script.exists() {
        return (StatusCode::SERVICE_UNAVAILABLE,
                Json(json!({"started": false, "error": "run_funnel.py not found"}))).into_response();
    }
    match interpreter
        .arg(&script)
        .arg("--window-utc").arg("05:00-17:00")
        .arg("--t-gate")
        .current_dir(&st.root)
        .stdin(std::process::Stdio::null())
        .spawn()
    {
        Ok(child) => (StatusCode::ACCEPTED,
                      Json(json!({"started": true, "pid": child.id()}))).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR,
                   Json(json!({"started": false, "error": e.to_string()}))).into_response(),
    }
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
    let state = AppState {
        tx,
        current: Arc::new(Mutex::new(build(&root).to_string())),
        root: root.clone(),
        port,
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

fn router(state: AppState) -> Router {
    Router::new()
        .route("/", get(index))
        .route("/architecture", get(architecture))
        .route("/api/snapshot", get(snapshot))
        .route("/api/scan", post(trigger_scan))
        .route("/api/logs/:which", get(full_log))
        .route("/api/report/latest", get(latest_report))
        .route("/ws", get(ws))
        .route("/vendor/react.production.min.js", get(|| js("react")))
        .route(
            "/vendor/react-dom.production.min.js",
            get(|| js("react-dom")),
        )
        .route("/vendor/htm.umd.js", get(|| js("htm")))
        .layer(middleware::from_fn_with_state(state.clone(), local_request))
        .with_state(state)
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
        assert!(display_engine(Value::Null).is_null());
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
        let row = "2026-11-09T12:00:00Z | scan_id=fixture | PROPOSAL".to_string();
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
        let st = AppState {
            tx,
            current: Arc::new(Mutex::new(build(&root).to_string())),
            root: root.clone(),
            port,
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
