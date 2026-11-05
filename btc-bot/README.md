# BTC Flow Regime bot (Alpaca PAPER)

An automated BTC strategy built on **market flow**: who is hitting the market, aggressive buyers or aggressive sellers.
It includes a reproducible backtest, walk-forward validation, and a bot that trades **BTC/USD on an Alpaca paper account**.

**Full results:** [`reports/REPORT.md`](reports/REPORT.md) (regenerate any time with `python research.py`).

> Educational project, not financial advice. The bot is hard-wired to Alpaca's **paper** endpoint and refuses the live one.

## The strategy in one paragraph
Every Binance trade is tagged as a buyer-initiated or seller-initiated *taker* trade. Summing those over **14 days** of 4h bars
gives a flow imbalance: net aggressive buying as a % of volume. Perp flow has a built-in sell-side bias, so the bot compares it with
its own **trailing 1-year median** (`flow_dev`). The bot goes **long** when perp flow is more than 0.5% buy-side of normal,
**spot** flow is also above normal (real spot demand, not just leverage), and price is above its 200-bar EMA. It goes **flat** when
perp flow turns 0.5% sell-side of normal, or a wide ATR disaster/trailing stop is hit. Size uses **volatility targeting** (50%
annualised), capped at 100% of equity with no leverage. Alpaca crypto can't be shorted, and shorting also failed out-of-sample anyway.

## Results snapshot (after 0.25% fee + 0.05% slippage per side)
| | CAGR | Sharpe | Max drawdown |
|---|---|---|---|
| Strategy, out-of-sample 2023 → Nov 2026 | +31.7% | 1.26 | −30.7% |
| Buy & hold, same period | +51.0% | 1.12 | −53.4% |
| Strategy, full 2020-06 → Nov 2026 | +26.5% | 1.10 | −30.7% |
| Buy & hold, full period | +39.3% | 0.85 | −77.1% |
| Strategy, walk-forward 2022 → now (yearly re-fit) | +6.1% | 0.38 | −34.8% |

Read this honestly. Buy-and-hold made more money in what was mostly a bull market. The strategy gives up part of the upside to
avoid most of the crashes: −23% vs −65% in 2022. Profitability across the parameter grid is robust (100% of 108 configs positive
out-of-sample), but the walk-forward and bootstrap intervals are wide. Treat it as a **risk-managed BTC exposure engine**, not a
money printer. See REPORT.md §8 for limitations.

## Layout
```
btc_bot/data.py        download + cache Binance bulk archive (klines w/ taker buy volume, funding)
btc_bot/live_data.py   archive + REST top-up for the live bot
btc_bot/features.py    flow imbalance, flow_dev, CVD, funding z, ATR, realised vol, EMAs
btc_bot/strategy.py    Flow Regime rules + DEFAULT parameters (documented)
btc_bot/backtest.py    bar-by-bar engine: next-open fills, intrabar stops with gap fills, fees, vol sizing
btc_bot/alpaca.py      paper-only Alpaca client (stdlib)
research.py            full study -> reports/REPORT.md, equity/drawdown SVGs, trades/grid CSVs
bot.py                 the trading bot (dry-run by default)
tests/                 no-lookahead, fill/fee accounting, paper-only safety
```

## Quick start
```bash
pip install -r requirements.txt
python research.py                  # ~1 min first time (downloads ~7 years of data into ./data)
python -m unittest discover -s tests
python bot.py                       # dry run: shows the live signal, no orders
```

### Paper trading
1. In Alpaca, switch to the **Paper** account and generate API keys.
2. `cp .env.example .env` and paste the keys.
3. `python bot.py --status` checks the connection.
4. `python bot.py --trade` runs one decision cycle and places paper orders if the signal says so.
5. Automate it **hourly**. Decisions happen only on closed 4h bars, so extra runs are harmless:
   - macOS/Linux cron: `5 * * * * cd /path/to/btc-bot && /usr/bin/python3 bot.py --trade >> bot_output.txt 2>&1`
   - Windows: Task Scheduler → run `run_bot.bat` every hour.

Options: `--max-notional 25000` caps dollars per position. `--halt-dd 0.35` sets the kill switch: flatten and stop if equity
falls 35% from its peak, beyond the backtest's worst drawdown. `--reset-halt` re-arms it after you've reviewed what happened.
Every action is logged to `bot_log.csv`. Bot state is kept in `state.json`.

### Data notes
- Signal data comes from Binance (BTCUSDT perp + spot) via `data.binance.vision` (bulk archive) and `data-api.binance.vision`
  (spot REST). Both are reachable from the US. Execution happens on Alpaca BTC/USD.
- Binance's futures REST API (`fapi.binance.com`) is blocked from the US. The bot then uses the archive's perp flow, which is up
  to ~1 day stale. The backtest shows this costs little (OOS Sharpe 1.26 → 1.15) because the signal is a 14-day window.

## Suggested path before ever considering real money
1. Run on paper for **at least 3–6 months**. With roughly 6 trades a year, the backtest itself has only ~39 trades.
2. Compare live paper fills and returns with what `research.py` would have done over the same dates.
3. Re-run `research.py` monthly. If out-of-sample behaviour drifts outside the tested ranges, stop.
