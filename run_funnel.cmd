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
REM DX-03: bound the append log at 5 MiB (one rotated .1 kept) and stamp each
REM run with an ISO time, so a failure carries a date. The live log is only
REM moved before the run starts, never deleted mid-run.
if exist "funnel_scheduler.out" for %%A in ("funnel_scheduler.out") do if %%~zA GTR 5242880 move /y "funnel_scheduler.out" "funnel_scheduler.out.1" >nul 2>&1
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format o" 2^>nul') do set "RUN_TS=%%I"
if not defined RUN_TS set "RUN_TS=%DATE% %TIME%"
echo ==== run %RUN_TS% ==== >> funnel_scheduler.out
REM --t-gate, same as the dashboard button: the live exit policy has no fixed
REM target, so a binomial z benchmarks a phantom break-even. One scan, one
REM statistic - two runners disagreeing about the gate was the bug.
".venv\Scripts\python.exe" run_funnel.py --window-utc 05:00-17:00 --t-gate >> funnel_scheduler.out 2>&1
exit /b %errorlevel%
