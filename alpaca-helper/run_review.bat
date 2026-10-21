@echo off
REM Trading Practice Academy - Alpaca PAPER review (read-only). Double-click or run from cmd.
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause
  exit /b 1
)
if not exist ".env" (
  echo No .env file found. Copy .env.example to .env and paste your PAPER keys first. See README.md.
  pause
  exit /b 1
)
python alpaca_review.py %*
echo.
echo Done. Import trades.json in the Academy: Review Gate ^> Import Alpaca paper trades.
pause
