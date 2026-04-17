@echo off
REM Toke Homer Sleep Agents — Nightly Batch Runner
REM Invoked by Windows Task Scheduler (schtasks) at 04:00 daily.
REM Runs Aurora (routing weight tuner), Hesper (learnings distiller), Nyx (theater auditor).
REM Each agent writes to its own reports directory with timestamped output.
REM Log output goes to Toke/automations/homer/sleep/nightly.log for debugging.

set SCRIPT_DIR=%~dp0
set LOG_FILE=%SCRIPT_DIR%nightly.log

echo ===== Nightly Sleep Run %DATE% %TIME% ===== >> "%LOG_FILE%"

python "%SCRIPT_DIR%sleep_cli.py" run all >> "%LOG_FILE%" 2>&1

echo ===== Complete %DATE% %TIME% ===== >> "%LOG_FILE%"
echo. >> "%LOG_FILE%"

exit /b 0
