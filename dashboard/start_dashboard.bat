@echo off
REM Launched at logon from HKCU\Software\Microsoft\Windows\CurrentVersion\Run.
REM
REM ZTT_ROOT points at the EXECUTION environment, not this repo: the dashboard
REM tails engine_state.json / funnel_history.log / position_manager.log, which
REM the engine writes outside this tree. Derived relative to this file
REM (dashboard -> repo -> _publish -> execution dir); override the env var if
REM the repo lives somewhere else. Point it at the repo and the heartbeat shows
REM permanently stale.
set "HERE=%~dp0"
if not defined ZTT_ROOT set "ZTT_ROOT=%HERE%..\..\.."

if not exist "%HERE%target\release\zero-trust-dashboard.exe" (
    echo Dashboard binary not built. Run: cd dashboard ^&^& cargo build --release
    pause
    exit /b 1
)

REM Minimised so logon does not flash a console window in your face.
start "" /min "%HERE%target\release\zero-trust-dashboard.exe" --port 8787
