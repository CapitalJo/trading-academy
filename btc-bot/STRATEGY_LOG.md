# Strategy log

Every BTC strategy considered for this bot, its status, and the evidence behind the decision. Machine-readable copy: `strategies.json`.
The bot stamps each row of `bot_log_<id>.csv` with the strategy id, version and fingerprint, so live results always trace back to an entry here.

Status: **PROCEED** = paper trade it · **CANDIDATE** = passed tests, needs your decision · **PARKED** = sound but blocked · **REJECTED** = failed, do not trade.

**Being paper traded:** `S1`, `S2`

| ID | Strategy | Status | Decided | Key evidence | Next step |
|---|---|---|---|---|---|
| S1 | Flow Regime v1.0, spot long/flat (Alpaca) | **PROCEED** | 2026-11-06 | 2023-26 out-of-sample: win 45%, Sharpe 1.48, max DD -18.6%, +28%/yr. Full: win 42%, DD -20.5%. Walk-forward 2022-now: +7.8%/yr, Sharpe 0.55, DD -26.8%. Monte Carlo 1-in-20 DD -34%. Beat plain trend rules out-of-sample. | Sign off (python signoff.py --strategy S1 --approve), then paper trade in Alpaca paper account #1 alongside S2 for 3-6 months. |
| S2 | Flow Regime v1.1 = v1.0 + take 1/3 profit at +2 ATR, rest to breakeven | **PROCEED** | 2026-11-06 | Win 67% (2020-22) / 65% (2023-26); max DD -10.6% / -13.9%; Monte Carlo 1-in-20 DD -28%; avg loss -3.6%, payoff 1.5x. Walk-forward 2022-now weaker than S1: +3.2%/yr, Sharpe 0.28, DD -27.5% (S1: +7.8%, 0.55, -26.8%). | Sign off (python signoff.py --strategy S2 --approve), then paper trade in Alpaca paper account #2 alongside S1. Compare with python compare.py --parity. |
| S3 | Flow Regime perp long/short (bull long, bear short, range pause) | **PARKED** | 2026-11-05 | 2022 bear year about 0% vs -17% (S1) and -65% (BTC). But shorts added about 0 in 2023-26: crash insurance, not profit. Needs a venue that can short (IBKR CME micro futures or Coinbase/Kraken US perps). | Revisit after S1/S2 has a live paper record. |
| S4 | Order-book entry timing (limit orders when the book favours us) | **PARKED** | 2026-11-06 | Not tested. Could save a few basis points per trade on S1/S2 entries. Execution improvement, not a strategy. | Low priority; only after paper trading shows fill quality. |
| R1 | Order Book Imbalance (OBI) scalping, 2-4 tick targets | **REJECTED** | 2026-11-06 | Fees about $76-380 per BTC round trip vs a $0.20-0.40 target: loses even at 100% win rate. Needs microsecond colocation; walls get spoofed. No tick-level book history to backtest. | None. |
| R2 | Bollinger RSI Exhaustion (BB 20/2.5 + RSI7 <20/>80, exit at basis, 1.5 ATR stop) | **REJECTED** | 2026-11-06 | BTC 1h as written: win 37-39%, -39% to -55%/yr, DD -86% to -88%. Loses even with zero fees (2023-26: -20%/yr, DD -66%). 4h: win 42%, -8%/yr, DD -38%. research_alternatives/bollinger_rsi_exhaustion.py | None. |
| R3 | RSI dip-buying in uptrends (with and without flow filter) | **REJECTED** | 2026-11-06 | With flow filter: 0 of 108 configs profitable 2023-26. Without: 7% of 162 (Alpaca costs), 15% (perp costs); the IS-best config lost out-of-sample. | None. |
| R4 | Funding carry / cash-and-carry basis trade | **REJECTED** | 2026-11-06 | Best rule +2%/yr 2020-26, about 0% in 2025-26; other rules negative. Reports put 2026 basis yields below Treasury rates. Needs a perp account too. | Re-check only if funding stays strongly positive for months. |
| R5 | Plain trend following (above 200 EMA / 200-day average) | **REJECTED** | 2026-11-06 | Looked best in 2020-22 (Sharpe 1.32) and collapsed in 2023-26 (Sharpe 0.50, DD -44%). 150-190 trades out-of-sample = fee drag. research_alternatives/trend_baselines.py | None. Kept as the benchmark S1 must keep beating. |
| R6 | Spot-only taker flow signal | **REJECTED** | 2026-11-05 | 2023-26 Sharpe 0.54 vs 1.26 with perp flow. | None. |
| R7 | Early v0: z-scored flow with 2-day holds | **REJECTED** | 2026-11-05 | 2023-26 about flat (Sharpe 0.18); 151 trades at 0.6% round trip ate the edge. | None. Led to the slower S1 design. |
| R8 | Pairs trading / statistical arbitrage: ETH vs BTC (z-score of the spread, enter |z|>2, exit at 0) | **REJECTED** | 2026-11-06 | Fails its own cointegration requirement: Engle-Granger ADF t = -1.2 (2020-22), -2.3 (2023-26), -2.5 (full) vs -3.34 needed; cointegrated in only 10% of rolling 1-year windows. As described (daily): win 13% / 42%, -31% / -16% per yr, DD -66% / -50%. Grid: 8% of 48 configs profitable 2023-26 on daily and 4h bars. ETH/BTC trended down for years, so 'buy the cheap one' kept losing. Needs shorting (not possible on Alpaca crypto). research_alternatives/pairs_btc_eth.py | None for BTC/ETH. Stock pairs (KO/PEP etc.) are a separate project: Alpaca can short stocks, but each pair must pass the same cointegration and out-of-sample tests. |
| R9 | Session Liquidity Sweep (Asian 00-07 UTC range; London/NY sweep-and-reclaim on 1m; stop $20 beyond wick; 50% at midpoint, rest at opposite boundary; 1% risk; 2-loss daily stop) | **REJECTED** | 2026-11-06 | BTC perp 1m, 2,700+ trades, rules as written with cheap futures fees: win 23% (2020-22) / 18% (2023-26), -0.47R / -0.60R per trade, -87% / -93% per yr, DD -99.5% / -100%. Zero fees: win 21%, -0.01R per trade (no raw edge). Median stop 0.21% of price, so fees alone cost ~0.6R per trade. Weekdays-only, London-only, NY-only and breakeven variants all lose similarly. Forex not tested (no forex data in this project). research_alternatives/session_liquidity_sweep.py | None for BTC. |

## Rules tested and rejected inside the Flow Regime strategy

- Exiting open trades when consolidation starts (hurt every variant)
- Funding-crowding entry veto (no benefit)
- Tight 3/5 ATR stops (2023-26 Sharpe 0.71 vs 1.48)
- Yearly parameter re-optimisation (chased noise; fixed parameters were more stable)

## Adding a new strategy

1. Add an entry to `strategies.json` with status `CANDIDATE` and its source.
2. Test it the same way: choose settings on 2020-22 only, report 2023-26 untouched, include costs, and show win rate, average win/loss and Monte Carlo drawdown.
3. Record the verdict and evidence, then regenerate this file: `python strategy_log.py`.
4. Add it to `VARIANTS` in `btc_bot/strategy.py`; only a signed-off fingerprint (`python signoff.py --strategy <id> --approve`) can trade.
