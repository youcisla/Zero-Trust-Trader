@echo off
REM Advisory digest of this tree's own diagnostic. Read-only by construction:
REM the shell runs status.py, and the model is given the Read tool and nothing
REM else (--tools "Read"), so it cannot run a terminal call, write a file, or
REM place an order. AGENTS.md §1 still owns execution and §4.7 still holds:
REM no scheduled entry may call an order function, and this one has no way to.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Missing project interpreter. Run: py -3.14 -m venv .venv
    exit /b 2
)

REM The facts come from the repository, not from the model.
".venv\Scripts\python.exe" status.py > status_digest.out 2>&1
if errorlevel 1 (
    echo status.py failed; no digest written.
    exit /b 1
)

REM Numbers must already be in the file. A digest that invents a z-score is the
REM one failure this project exists to prevent, so the prompt forbids inference
REM and the tool list forbids action.
claude -p "Read status_digest.out and summarize in at most 15 lines: engine age, last verdict, trials and the z bar, open positions, and any line that reads as a refusal or an alert. Use only numbers present in the file; write 'unknown' where the file is silent. Do not recommend an order: this is a monitor." --tools "Read" --max-turns 4 >> digest.out 2>&1
exit /b %errorlevel%
