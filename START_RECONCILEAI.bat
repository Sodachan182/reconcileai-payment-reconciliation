@echo off
REM ReconcileAI one-click launcher. Creates the virtual environment on first run,
REM installs dependencies only when requirements change, then opens the app.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1"
if errorlevel 1 (
  echo.
  echo ReconcileAI could not start. See the message above.
  pause
)
endlocal
