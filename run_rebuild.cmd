@echo off
REM Daily universe rebuild: Tier 1 (whole broker) then the liquid top-50 cache.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Missing project interpreter. Run: py -3.14 -m venv .venv
    exit /b 2
)
".venv\Scripts\python.exe" run_funnel.py --rebuild --top 50 >> funnel_rebuild.out 2>&1
exit /b %errorlevel%
