@echo off
REM Intraday M15 scan: Tier 2 + Tier 3 over the cached liquid universe.
REM 05:00-17:00 UTC (Asian open through the NY overlap). The strategy matrix
REM narrows by session inside run_funnel.py; it exits 0 outside the window
REM and takes funnel.lock, so overlapping fires cost nothing.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Missing project interpreter. Run: py -3.14 -m venv .venv
    exit /b 2
)
REM --t-gate, same as the dashboard button: the live exit policy has no fixed
REM target, so a binomial z benchmarks a phantom break-even. One scan, one
REM statistic - two runners disagreeing about the gate was the bug.
".venv\Scripts\python.exe" run_funnel.py --window-utc 05:00-17:00 --t-gate >> funnel_scheduler.out 2>&1
exit /b %errorlevel%
