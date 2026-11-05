//! Local read-only dashboard for the Zero-Trust Trader engine.
//!
//! Design rule: **the Python engine must never know this process exists.**
//! There is no IPC, no socket the engine connects to, no callback. The engine
//! writes ordinary files as part of its normal work; this process tails them.
//! Kill the dashboard and the trading system does not notice. Kill the engine
//! and the dashboard shows a stale-data age instead of lying.
//!
//! Endpoints
//!   GET /              the UI (single embedded HTML file, React from CDN)
//!   GET /api/snapshot  current state, one shot
//!   GET /ws            streaming updates, pushed only when something changes

use axum::{
    extract::{
        ws::{Message, WebSocket, WebSocketUpgrade},
        State,
    },
    response::{Html, IntoResponse},
    routing::get,
    Json, Router,
};
use serde_json::{json, Value};
use std::{
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

fn tail(path: &Path, n: usize) -> Vec<String> {
    // ponytail: blocking read on an async runtime. Files are log-sized and this
    // runs once a second; move to spawn_blocking if logs ever reach megabytes.
    match std::fs::read_to_string(path) {
        Ok(s) => {
            let lines: Vec<&str> = s.lines().filter(|l| !l.trim().is_empty()).collect();
            lines[lines.len().saturating_sub(n)..]
                .iter()
                .map(|l| l.to_string())
                .collect()
        }
        Err(_) => Vec::new(),
    }
}

fn age_secs(path: &Path) -> Option<u64> {
    let m = std::fs::metadata(path).ok()?.modified().ok()?;
    SystemTime::now().duration_since(m).ok().map(|d| d.as_secs())
}

fn build(root: &Path) -> Value {
    let engine_path = root.join("engine_state.json");
    let engine: Value = std::fs::read_to_string(&engine_path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or(Value::Null);

    let positions = engine
        .get("positions")
        .and_then(Value::as_array)
        .map(|v| v.len())
        .unwrap_or(0);
    let equity = engine.get("account").and_then(|a| a.get("equity")).cloned();
    let balance = engine.get("account").and_then(|a| a.get("balance")).cloned();
    let currency = engine
        .get("account")
        .and_then(|a| a.get("currency"))
        .cloned()
        .unwrap_or(json!(""));
    let server = engine.get("account").and_then(|a| a.get("server")).cloned();

    // The bridge writes audit.jsonl next to its config when a mutating tool is
    // called; it may legitimately not exist yet.
    let audit_candidates = [
        root.join("audit.jsonl"),
        std::env::var("APPDATA")
            .map(|p| PathBuf::from(p).join("mt5-mcp").join("audit.jsonl"))
            .unwrap_or_default(),
    ];
    let mcp = audit_candidates
        .iter()
        .find(|p| p.exists())
        .map(|p| tail(p, TAIL))
        .unwrap_or_default();

    json!({
        "ts": SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0),
        "engine": engine,
        "engine_age_s": age_secs(&engine_path),
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
        .route("/api/snapshot", get(snapshot))
        .route("/ws", get(ws))
        .route("/vendor/react.production.min.js", get(|| js("react")))
        .route("/vendor/react-dom.production.min.js", get(|| js("react-dom")))
        .route("/vendor/htm.umd.js", get(|| js("htm")))
        .with_state(state);

    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    println!("zero-trust dashboard  http://localhost:{port}");
    println!("watching              {}", root.display());
    if let Ok(l) = tokio::net::TcpListener::bind(addr).await {
        let _ = axum::serve(l, app).await;
    } else {
        eprintln!("port {port} unavailable");
    }
}
