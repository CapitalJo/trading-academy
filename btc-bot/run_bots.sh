#!/usr/bin/env bash
# BTC Flow Regime bots - Alpaca PAPER. Schedule hourly with cron:
#   5 * * * * /path/to/btc-bot/run_bots.sh
cd "$(dirname "$0")"
for s in S1 S2; do python3 bot.py --strategy "$s" --trade "$@" >> "bot_output_$s.txt" 2>&1; done
python3 dashboard.py >> bot_output_dashboard.txt 2>&1
