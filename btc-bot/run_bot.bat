@echo off
REM BTC Flow Regime bots - Alpaca PAPER. Schedule this hourly in Windows Task Scheduler.
REM Runs S1 and S2, each in its own paper account (.env.S1 / .env.S2).
cd /d "%~dp0"
python bot.py --strategy S1 --trade %* >> bot_output_S1.txt 2>&1
python bot.py --strategy S2 --trade %* >> bot_output_S2.txt 2>&1
