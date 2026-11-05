@echo off
REM Daily universe rebuild: Tier 1 (whole broker) then the liquid top-50 cache.
cd /d "%~dp0"
python run_funnel.py --rebuild --top 50 >> funnel_rebuild.out 2>&1
