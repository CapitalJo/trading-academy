# BTC Flow Regime bot (Alpaca PAPER)

An automated BTC strategy built on **market flow**: who is hitting the market, aggressive buyers or aggressive sellers. It trades
bull and bear regimes and **pauses during consolidation**.
It includes a reproducible backtest, walk-forward validation, and a bot that trades **BTC/USD on an Alpaca paper account**.

**Full results:** [`reports/REPORT.md`](reports/REPORT.md) (regenerate any time with `python research.py`).

> Educational project, not financial advice. The bot is hard-wired to Alpaca's **paper** endpoint and refuses the live one.

## The strategy
Every Binance trade is tagged as a buyer-initiated or seller-initiated *taker* trade. Summing those over **14 days** of 4h bars
gives a flow imbalance: net aggressive buying as a % of volume. Perp flow has a built-in sell-side bias, so the bot compares it with
its own **trailing 1-year median** (`flow_dev`).

Each 4h close, the market is classified into a regime that decides **which side may trade, or whether to trade at all**:

| Regime | Rule | What the bot does |
|---|---|---|
| **BULL** | price above 200-bar EMA, ADX(14) ≥ 20 | long when perp flow is >0.5% buy-side of normal and spot flow agrees |
| **BEAR** | price below a *falling* 200-bar EMA, ADX ≥ 20 | short when flow is >0.5% sell-side of normal (perp venue). **On Alpaca: cash**, since spot can't be shorted |
| **RANGE** | ADX < 20 (consolidation), or mixed signals | **pause**: no new trades. Open trades keep their normal exits |

Exits: flow crosses back 0.5% to the other side, or a wide ATR disaster/trailing stop (5 ATR initial, 8 ATR trail). Size uses
**volatility targeting** (50% annualised), capped at 100% of equity with no leverage. Exiting positions *because* a range started was
tested and hurts (REPORT §4), so the pause only blocks new entries.

## Results snapshot (after costs)
| | CAGR | Sharpe | Max DD | 2022 (bear year) |
|---|---|---|---|---|
| Spot long/flat (Alpaca), out-of-sample 2023 → Nov 2026 | +35.0% | 1.41 | −24.9% | — |
| Perp long/short, out-of-sample 2023 → Nov 2026 | +31.8% | 1.03 | −28.9% | — |
| Buy & hold, same period | +51.0% | 1.12 | −53.4% | — |
| Spot long/flat, full 2020-06 → now | +28.9% | 1.20 | −30.7% | −25.6% |
| Perp long/short, full 2020-06 → now | +32.9% | 1.07 | −28.9% | **−2.3%** |
| Buy & hold, full period | +39.3% | 0.85 | −77.1% | −64.7% |
| Walk-forward 2022 → now (yearly re-fit): long/flat / long/short | +7.5% / +11.4% | 0.44 / 0.51 | −38% / −39% | |
| Buy & hold, 2022 → now | +11.9% | 0.47 | −67.2% | |

What this means:
- **Bear markets:** shorting roughly neutralised 2022 (−2% vs −26% long-only, −65% buy-and-hold). But out-of-sample in 2023–26,
  short trades added nothing (−2.6% total). Shorts mainly work as crash insurance, not as a profit engine.
- **Consolidation pause:** about a third of the time is classified RANGE. Pausing improved the spot strategy (OOS Sharpe 1.26 → 1.41,
  max DD −31% → −25%) and was roughly neutral for long/short. It's a modest filter, not a big edge.
- **Overall:** buy-and-hold made more money in this mostly-bull window. The strategy's advantage is avoiding most of the crashes.
  The walk-forward test is much weaker than the fixed-parameter numbers. See REPORT.md §10 for limitations.

## Layout
```
btc_bot/data.py        download + cache Binance bulk archive (klines w/ taker buy volume, funding)
btc_bot/live_data.py   archive + REST top-up for the live bot
btc_bot/features.py    flow imbalance, flow_dev, CVD, funding z, ATR, realised vol, EMAs
btc_bot/strategy.py    regime classifier (BULL/BEAR/RANGE), Flow Regime rules, venue PROFILES, DEFAULTS
btc_bot/backtest.py    bar-by-bar engine: next-open fills, intrabar stops with gap fills, fees, vol sizing
btc_bot/alpaca.py      paper-only Alpaca client (stdlib)
research.py            full study -> reports/REPORT.md, equity/drawdown SVGs, trades/grid CSVs
bot.py                 the trading bot (dry-run by default)
tests/                 no-lookahead, regime consistency, fill/fee accounting, paper-only safety
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
1. Run on paper for **at least 3–6 months**. With roughly 6 trades a year, the backtest itself has only ~36 trades on the spot profile.
2. Compare live paper fills and returns with what `research.py` would have done over the same dates.
3. Re-run `research.py` monthly. If out-of-sample behaviour drifts outside the tested ranges, stop.
