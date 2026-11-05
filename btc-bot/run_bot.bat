@echo off
REM BTC Flow Regime bot - Alpaca PAPER. Schedule this hourly in Windows Task Scheduler.
cd /d "%~dp0"
python bot.py --trade %* >> bot_output.txt 2>&1
