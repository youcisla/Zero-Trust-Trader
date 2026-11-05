@echo off
REM Intraday M15 scan: Tier 2 + Tier 3 over the cached liquid universe.
REM 05:00-17:00 UTC (Asian open through the NY overlap). The strategy matrix
REM narrows by session inside run_funnel.py; it exits 0 outside the window
REM and takes funnel.lock, so overlapping fires cost nothing.
cd /d "%~dp0"
python run_funnel.py --window-utc 05:00-17:00 >> funnel_scheduler.out 2>&1
