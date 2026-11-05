@echo off
REM Launched at logon from HKCU\Software\Microsoft\Windows\CurrentVersion\Run.
REM
REM ZTT_ROOT points at the EXECUTION environment, not this repo: the dashboard
REM tails engine_state.json / funnel_history.log / position_manager.log, and the
REM engine runs from %APPDATA%\mt5-mcp. Point it at the repo and the heartbeat
REM shows permanently stale.
set "HERE=%~dp0"
set "ZTT_ROOT=%APPDATA%\mt5-mcp"

if not exist "%HERE%target\release\zero-trust-dashboard.exe" (
    echo Dashboard binary not built. Run: cd dashboard ^&^& cargo build --release
    pause
    exit /b 1
)

REM Minimised so logon does not flash a console window in your face.
start "" /min "%HERE%target\release\zero-trust-dashboard.exe" --port 8787
