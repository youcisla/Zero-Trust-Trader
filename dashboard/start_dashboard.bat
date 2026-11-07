@echo off
REM Launched at logon from HKCU\Software\Microsoft\Windows\CurrentVersion\Run.
REM
REM ZTT_ROOT points at this tree, which is both the repo and the execution
REM environment: engine_state.json, funnel_history.log and position_manager.log
REM are written at the repo root. Derived from this file (dashboard -> repo).
REM The old chain (dashboard -> repo -> _publish -> %APPDATA%\mt5-mcp) is gone;
REM pointing ZTT_ROOT anywhere else shows a permanently stale heartbeat.
set "HERE=%~dp0"
if not defined ZTT_ROOT set "ZTT_ROOT=%HERE%.."

if not exist "%HERE%target\release\zero-trust-dashboard.exe" (
    echo Dashboard binary not built. Run: cd dashboard ^&^& cargo build --release
    pause
    exit /b 1
)

REM Minimised so logon does not flash a console window in your face.
start "" /min "%HERE%target\release\zero-trust-dashboard.exe" --port 8787
