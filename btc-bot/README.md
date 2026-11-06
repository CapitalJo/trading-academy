# BTC Flow Regime bot (Alpaca PAPER)

An automated BTC strategy built on **market flow**: who is hitting the market, aggressive buyers or aggressive sellers. It trades
bull and bear regimes and **pauses during consolidation**.
It includes a reproducible backtest, walk-forward validation, and a bot that trades **BTC/USD on an Alpaca paper account**.

**Strategy log:** [`STRATEGY_LOG.md`](STRATEGY_LOG.md) · **Full results:** [`reports/REPORT.md`](reports/REPORT.md) (regenerate any time with `python research.py`).

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
**volatility targeting** (30% annualised), capped at 100% of equity with no leverage. Exiting positions *because* a range started was
tested and hurts (REPORT §4), so the pause only blocks new entries.

## Results snapshot: v1.0 (target vol 30%, after costs)
| | CAGR | Sharpe | Win rate | Max DD | 2022 (bear year) |
|---|---|---|---|---|---|
| **Spot long/flat (Alpaca)**, out-of-sample 2023 → Nov 2026 | +28.4% | 1.48 | 45% | −18.6% | — |
| Spot long/flat, full 2020-06 → now | +22.0% | 1.26 | 42% | −20.5% | −17.1% |
| Spot long/flat, walk-forward 2022 → now (yearly re-fit) | +7.8% | 0.55 | — | −26.8% | |
| Perp long/short (needs a short venue), full 2020-06 → now | +25.2% | 1.11 | 44% | −22.4% | **+0.0%** |
| Buy & hold BTC, full period | +39.3% | 0.85 | — | −77.1% | −64.7% |

- **Win rate is low by design (36–45%).** The edge is the payoff ratio: average loss about −3.5%, held steady in-sample and
  out-of-sample; average win about +15%.
- **Plan for a −34% drawdown**, the Monte Carlo 1-in-20 path, not the −20.5% backtest figure.
- **Bear markets:** on Alpaca the bot holds cash. Perp long/short roughly neutralised 2022, but its shorts added nothing in 2023–26.
- **Consolidation pause:** about a third of the time is RANGE. Pausing improved the spot version modestly.
- Buy-and-hold made more money in this mostly-bull window. The strategy's advantage is avoiding most of the crashes.

## Sign-off and survival limits
`python signoff.py` writes [`reports/SIGNOFF.md`](reports/SIGNOFF.md): the exact rules, the win-rate and drawdown evidence, and
**survival limits fixed before any live trading**:
- warn at −22% drawdown, **halt at −34%**;
- warn at 9 consecutive losses, **halt at 13**;
- **halt** on any single loss worse than −8.8%;
- review if the average loss or the win rate falls outside its backtest band.

Once you've reviewed it, run `python signoff.py --approve "Your Name"`. That writes `SIGNED_OFF.json` with the strategy's parameter
**fingerprint**. `bot.py --trade` refuses to run unless the fingerprint matches, so changing any parameter needs a new sign-off.

## Layout
```
btc_bot/data.py        download + cache Binance bulk archive (klines w/ taker buy volume, funding)
btc_bot/live_data.py   archive + REST top-up for the live bot
btc_bot/features.py    flow imbalance, flow_dev, CVD, funding z, ATR, realised vol, EMAs
btc_bot/strategy.py    regime classifier (BULL/BEAR/RANGE), Flow Regime rules, venue PROFILES, DEFAULTS
btc_bot/backtest.py    bar-by-bar engine: next-open fills, intrabar stops with gap fills, fees, vol sizing
btc_bot/alpaca.py      paper-only Alpaca client (stdlib)
research.py            full study -> reports/REPORT.md, equity/drawdown SVGs, trades/grid CSVs
signoff.py             sign-off pack (reports/SIGNOFF.md), survival limits, --approve
STRATEGY_LOG.md        every strategy considered: PROCEED / CANDIDATE / PARKED / REJECTED + evidence
strategies.json        same registry, machine-readable; 'active' id is stamped on every bot_log.csv row
strategy_log.py        regenerates STRATEGY_LOG.md from strategies.json
research_alternatives/ reproducible tests of the rejected/candidate strategies
bot.py                 the trading bot (dry-run by default)
tests/                 no-lookahead, regime consistency, fill/fee accounting, survival limits, sign-off, paper-only safety
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
3. Review `reports/SIGNOFF.md`, then `python signoff.py --approve "Your Name"`.
4. `python bot.py --status` checks the connection and shows live results against the survival limits.
5. `python bot.py --trade` runs one decision cycle and places paper orders if the signal says so.
6. Automate it **hourly**. Decisions happen only on closed 4h bars, so extra runs are harmless:
   - macOS/Linux cron: `5 * * * * cd /path/to/btc-bot && /usr/bin/python3 bot.py --trade >> bot_output.txt 2>&1`
   - Windows: Task Scheduler → run `run_bot.bat` every hour.

Options: `--max-notional 25000` caps dollars per position. `--halt-dd 0.25` makes the drawdown halt stricter than the signed-off
limit (it can't be loosened). `--reset-halt` re-arms the bot after a survival-limit halt, once you've reviewed what happened.
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
