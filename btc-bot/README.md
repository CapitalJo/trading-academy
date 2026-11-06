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
Two strategies are being paper traded side by side (see [`STRATEGY_LOG.md`](STRATEGY_LOG.md)):

| | S1: Flow Regime v1.0 | S2: v1.1 (+ take ⅓ at +2 ATR, rest to breakeven) |
|---|---|---|
| Win rate 2020–22 / 2023–26 | 36% / 45% | 67% / 65% |
| Max drawdown 2020–22 / 2023–26 | −20.5% / −18.6% | −10.6% / −13.9% |
| Monte Carlo 1-in-20 drawdown | −34% | −28% |
| Walk-forward 2022 → now (the honest one) | +7.8%/yr, Sharpe 0.55 | +3.2%/yr, Sharpe 0.28 |
| **Halt** limits: drawdown / losing streak / single loss | −34% / 13 / −8.8% | −28% / 7 / −8.8% |
| Sign-off pack | [`reports/SIGNOFF_S1.md`](reports/SIGNOFF_S1.md) | [`reports/SIGNOFF_S2.md`](reports/SIGNOFF_S2.md) |

Each pack has the exact rules, the win-rate and drawdown evidence, and **survival limits fixed before any trading**. Approve each
one separately with `python signoff.py --strategy S1 --approve "Your Name"` (and the same for `S2`). That writes `SIGNED_OFF_<id>.json`
with the strategy's parameter **fingerprint**. `bot.py --trade` refuses to run a strategy unless its fingerprint matches, so
changing any parameter needs a new sign-off.

## Layout
```
btc_bot/data.py        download + cache Binance bulk archive (klines w/ taker buy volume, funding)
btc_bot/live_data.py   archive + REST top-up for the live bot
btc_bot/features.py    flow imbalance, flow_dev, CVD, funding z, ATR, realised vol, EMAs
btc_bot/strategy.py    regime classifier, Flow Regime rules, DEFAULTS, VARIANTS (S1, S2) with fingerprints
btc_bot/backtest.py    bar-by-bar engine: next-open fills, intrabar stops with gap fills, fees, vol sizing
btc_bot/alpaca.py      paper-only Alpaca client (stdlib)
research.py            full study -> reports/REPORT.md, equity/drawdown SVGs, trades/grid CSVs
signoff.py             sign-off pack per strategy (reports/SIGNOFF_<id>.md), survival limits, --approve
compare.py             S1 vs S2 paper scoreboard; --parity checks the bot traded on the backtest's bars
STRATEGY_LOG.md        every strategy considered: PROCEED / CANDIDATE / PARKED / REJECTED + evidence
strategies.json        same registry, machine-readable ('active' = strategies being paper traded)
strategy_log.py        regenerates STRATEGY_LOG.md from strategies.json
research_alternatives/ reproducible tests of the rejected/candidate strategies
bot.py                 the trading bot: --strategy S1|S2, dry-run by default
run_bot.bat / run_bots.sh  run S1 and S2 once (schedule hourly)
tests/                 no-lookahead, regime consistency, fill/fee accounting, survival limits, sign-off, paper-only safety
```

## Quick start
```bash
pip install -r requirements.txt
python research.py                  # ~1 min first time (downloads ~7 years of data into ./data)
python -m unittest discover -s tests
python bot.py --strategy S1         # dry run: shows the live signal, no orders
```

### Paper trading S1 and S2 side by side (Alpaca)
Both strategies trade BTC/USD, so each needs its **own** Alpaca paper account. In one account their positions would merge
and neither could be measured.

1. In the Alpaca dashboard, open the paper-account menu and create a **second paper account**. Reset both to the same
   starting balance, for example $10,000 each, so results compare directly.
2. Generate API keys in each paper account. Copy `.env.example` to **`.env.S1`** (keys from account 1) and to **`.env.S2`**
   (keys from account 2).
3. Review `reports/SIGNOFF_S1.md` and `reports/SIGNOFF_S2.md`, then approve each:
   `python signoff.py --strategy S1 --approve "Your Name"` and `python signoff.py --strategy S2 --approve "Your Name"`.
4. Check both connections: `python bot.py --strategy S1 --status` and `python bot.py --strategy S2 --status`.
5. Automate **hourly**. Decisions happen only on closed 4h bars, so extra runs are harmless:
   - Windows: Task Scheduler → run `run_bot.bat` every hour (it runs S1 then S2).
   - macOS/Linux cron: `5 * * * * /path/to/btc-bot/run_bots.sh`
6. Check progress any time: `python compare.py --live` (scoreboard) and `python compare.py --parity` (did the bots trade on the
   same bars the backtest would have?).

Each strategy keeps its own files: `state_<id>.json`, `bot_log_<id>.csv` (every row stamped with strategy id, version and
fingerprint), `SIGNED_OFF_<id>.json`, `bot_output_<id>.txt`. Options: `--max-notional 25000` caps dollars per position.
`--halt-dd 0.20` makes the drawdown halt stricter than the signed-off limit (it can't be loosened).
`--reset-halt` re-arms a strategy after a survival-limit halt, once you've reviewed what happened.

**Why not IBKR for these two:** both are spot long/cash strategies, and Alpaca's paper API already fits them with no extra
software. IBKR becomes the right platform for the parked long/short version (S3): CME Micro Bitcoin futures can be shorted.
It needs TWS or IB Gateway running, and its contracts are 0.1 BTC each.

### Data notes
- Signal data comes from Binance (BTCUSDT perp + spot) via `data.binance.vision` (bulk archive) and `data-api.binance.vision`
  (spot REST). Both are reachable from the US. Execution happens on Alpaca BTC/USD.
- Binance's futures REST API (`fapi.binance.com`) is blocked from the US. The bot then uses the archive's perp flow, which is up
  to ~1 day stale. The backtest shows this costs little (OOS Sharpe 1.26 → 1.15) because the signal is a 14-day window.

## Suggested path before ever considering real money
1. Run on paper for **at least 3–6 months**. With roughly 6 trades a year, the backtest itself has only ~36 trades on the spot profile.
2. Compare live paper fills and returns with what `research.py` would have done over the same dates.
3. Re-run `research.py` monthly. If out-of-sample behaviour drifts outside the tested ranges, stop.
