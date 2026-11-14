@echo off
REM Daily universe rebuild: Tier 1 (whole broker) then the liquid top-50 cache.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Missing project interpreter. Run: py -3.14 -m venv .venv
    exit /b 2
)
REM DX-03: bound the append log at 5 MiB (one rotated .1 kept) and stamp each
REM run with an ISO time. The live log is only moved before the run starts.
if exist "funnel_rebuild.out" for %%A in ("funnel_rebuild.out") do if %%~zA GTR 5242880 move /y "funnel_rebuild.out" "funnel_rebuild.out.1" >nul 2>&1
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format o" 2^>nul') do set "RUN_TS=%%I"
if not defined RUN_TS set "RUN_TS=%DATE% %TIME%"
echo ==== run %RUN_TS% ==== >> funnel_rebuild.out
REM --top 0 --pool 0: keep everything tier 1 returns (the old `--top 50` could not
REM reproduce the 248-symbol universe every document describes).
".venv\Scripts\python.exe" run_funnel.py --rebuild --top 0 --pool 0 >> funnel_rebuild.out 2>&1
exit /b %errorlevel%
