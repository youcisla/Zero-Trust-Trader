@echo off
REM Position supervisor -- DRY RUN. No --apply, so it can only print what it
REM would do; it cannot move a stop or close anything.
REM
REM DO NOT add this to Task Scheduler until there is a real open trade to
REM manage. When that day comes: schtasks /create /sc onstart ... (see AGENTS.md).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Missing project interpreter. Run: py -3.14 -m venv .venv
    exit /b 2
)
".venv\Scripts\python.exe" position_manager.py --interval 5 --session-close 17:00 >> position_manager.out 2>&1
exit /b %errorlevel%
