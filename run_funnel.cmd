@echo off
REM Intraday M15 scan: Tier 2 + Tier 3 over the cached liquid universe.
REM 13:00-17:00 UTC only (London/NY overlap). run_funnel.py exits 0 outside it
REM and takes funnel.lock, so overlapping fires cost nothing.
cd /d "%~dp0"
python run_funnel.py --window-utc 13:00-17:00 >> funnel_scheduler.out 2>&1
