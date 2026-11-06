# Strategy sign-off: BTC Flow Regime v1.0

Fingerprint **`ea6baed8d9d9`** · venue **Alpaca BTC/USD spot (long or cash)** · generated 2026-11-06 02:08 UTC · data 2019-12-31 → 2026-11-04 · costs 0.25% fee + 0.05% slippage per side

> Educational research, not financial advice. Backtests are estimates. This document fixes, **before** live trading, what results count as "the strategy is behaving as tested" and what counts as "it has stopped working".

## 1. The rules you are signing off

**Data:** Binance BTCUSDT perpetual and spot 1h klines (taker buy volume), aggregated into **4h bars**. Decisions are made only on closed bars.

**Flow signal:** `delta = taker_buy − taker_sell` per bar. `flow_imb = Σdelta / Σvolume` over the last **84 bars (14 days)**.
`flow_dev = flow_imb − median(flow_imb over the last 2190 bars ≈ 1 year)`. The same is computed on spot (`spot_dev`).

**Regime** (each bar):
- **BULL**: close > EMA(200) and ADX(14) ≥ 20.
- **BEAR**: close < EMA(200), EMA falling over 30 bars, ADX ≥ 20. On this venue (Alpaca spot): **hold cash**.
- **RANGE**: everything else. **Pause**: no new entries.

**Entry (long):** regime is BULL **and** `flow_dev > 0.005` **and** `spot_dev > 0`. Buy at the next bar's open.

**Position size:** `fraction = 30% ÷ BTC's annualised volatility (last 180 bars)`, capped at **100% of equity** (no leverage).
Example: BTC running at 60% volatility → 50% of equity.

**Exits** (whichever comes first):
1. **Flow exit:** `flow_dev < -0.005` at a bar close → sell at the next open.
2. **Initial stop:** entry − **5.0 × ATR(14)**. Rests on the exchange as a stop-limit order.
3. **Trailing stop:** highest close since entry − **8.0 × ATR**, ratchets up only.

Consolidation never forces an exit. Testing showed that exiting on consolidation hurts (REPORT.md §4).

**Not used** (tested, no benefit): funding-rate veto, shorting on spot, tight stops, spot-only flow.

<details><summary>Every parameter (this exact set produces the fingerprint)</summary>

| Parameter | Value |
|---|---|
| `timeframe` | 4h |
| `flow_n` | 84 |
| `base_n` | 2190 |
| `z_n` | 180 |
| `ema_fast` | 50 |
| `ema_slow` | 200 |
| `atr_n` | 14 |
| `don_n` | 20 |
| `entry_dev` | 0.005 |
| `exit_dev` | -0.005 |
| `spot_confirm` | True |
| `trend_filter` | True |
| `funding_max_z` | None |
| `stop_atr` | 5.0 |
| `trail_atr` | 8.0 |
| `sizing` | vol |
| `target_vol` | 0.3 |
| `allow_short` | False |
| `chop_adx_min` | 20 |
| `chop_er_min` | None |
| `short_entry_dev` | None |
| `short_exit_dev` | None |
| `short_slope` | True |
| `chop_exit` | False |

</details>

Code: `btc_bot/strategy.py` (rules), `btc_bot/features.py` (indicators), `btc_bot/backtest.py` (simulation), `bot.py` (execution).

## 2. Win rate: what to expect, and why it survives

**The win rate is low by design: 42% overall (36% in-sample, 45% out-of-sample).** The strategy survives on its **payoff ratio**, not its win rate. Losses are capped by the stop and the flow exit. Winners ride multi-week trends.

| | Trades | Win rate | Avg win | Avg loss | Payoff (avg win ÷ avg loss) | Expectancy per trade | Worst trade |
|---|---|---|---|---|---|---|---|
| In-sample 2020-06 → 2022 | 14 | 36% | +13.6% | -3.4% | 4.0× | +2.7% | -5.6% |
| **Out-of-sample 2023 → now** | 22 | 45% | +15.8% | -3.5% | 4.5× | +5.3% | -5.9% |
| Full | 36 | 42% | +15.1% | -3.5% | 4.3× | +4.3% | -5.9% |

The number that held from in-sample to out-of-sample is the **average loss**. The win rate moved (it was *higher* out-of-sample). The win rate swings a lot year to year, so don't judge the strategy on a handful of trades:

| Year | Trades | Win rate | Avg win | Avg loss | Sum of trade returns |
|---|---|---|---|---|---|
| 2020 | 3 | 67% | +18.1% | -3.9% | +32.2% |
| 2021 | 4 | 50% | +15.3% | -3.7% | +23.2% |
| 2022 | 7 | 14% | +1.6% | -3.3% | -18.3% |
| 2023 | 4 | 75% | +11.5% | -5.9% | +28.6% |
| 2024 | 7 | 43% | +26.0% | -2.9% | +66.7% |
| 2025 | 3 | 33% | +13.2% | -4.0% | +5.2% |
| 2026 | 8 | 38% | +10.9% | -3.4% | +15.8% |

Monte Carlo (20,000 random re-orderings of the backtest trades, 30 trades each, ≈ 4–5 years of trading): win rate 90% band **27% – 57%**. Longest losing streak: median 5, 1-in-20 **9**, 1-in-100 12 (backtest actual: 4). P(30 trades lose money overall): 3.0%.

**Why earlier strategies often fail on win rate:** high-win-rate systems usually take small, frequent profits and rare large losses. They look great until the large loss arrives, and then the drawdown doesn't survive. This one is built the other way round: frequent small, capped losses and rare large wins. Its failure mode is slow (a long string of small losses), which the limits in §5 catch early.

## 3. Drawdown: what to expect, and how bad it can get

| Measure | Value |
|---|---|
| Backtest max drawdown, in-sample | -20.5% |
| Backtest max drawdown, **out-of-sample** | -18.6% |
| Backtest max drawdown, full period | -20.5% |
| Buy-and-hold BTC max drawdown, same period | -77.1% |
| Monte Carlo max drawdown (daily returns resampled in 20-day blocks, 10,000 paths): typical | -20.6% |
| Monte Carlo: 1-in-20 bad path | **-33.8%** |
| Monte Carlo: 1-in-100 bad path | -41.6% |
| Longest time below a previous equity high | 455 days |

**Plan for a -33.8% drawdown, not -20.5%.** The backtest is one path; the Monte Carlo shows how unlucky ordering of the same kind of returns can hurt. Only trade a size where that number is survivable for you.

Drawdown by year (strategy vs buy-and-hold):

| Year | Strategy return | Strategy max DD | BTC return | BTC max DD |
|---|---|---|---|---|
| 2020 | +33.9% | -6.5% | +203.1% | -20.0% |
| 2021 | +23.3% | -7.2% | +57.7% | -54.2% |
| 2022 | -17.1% | -20.5% | -64.7% | -67.2% |
| 2023 | +28.0% | -7.3% | +156.0% | -21.3% |
| 2024 | +72.4% | -10.5% | +120.8% | -30.1% |
| 2025 | +4.3% | -10.8% | -6.6% | -34.4% |
| 2026 | +13.6% | -13.4% | -8.4% | -40.0% |

## 4. Position size is the drawdown dial

The rules are the same; only `target_vol` changes. Drawdown scales with size, while Sharpe barely moves. v1.0 uses **30%**.

| target_vol | CAGR (full) | Sharpe IS / OOS | Max DD (full) | Monte Carlo 1-in-20 DD |
|---|---|---|---|---|
| 20% | +16.2% | 0.89 / 1.52 | -14.1% | -24.0% |
| 30% ← v1.0 | +22.0% | 0.89 / 1.48 | -20.5% | -33.6% |
| 40% | +26.3% | 0.89 / 1.43 | -26.2% | -41.4% |
| 50% | +28.9% | 0.90 / 1.41 | -30.7% | -46.8% |

## 5. Survival limits (fixed now, enforced by the bot)

These are written to `reports/survival_limits.json`. On sign-off they're copied into `SIGNED_OFF.json`, which the bot reads.

| Check | Limit | Bot action | Why this number |
|---|---|---|---|
| Drawdown from equity peak | -22.0% | **Warn** in log/console | Beyond the worst backtest drawdown (-20.5%) |
| Drawdown from equity peak | -34.0% | **Halt**: flatten, stop trading | Beyond 95% of Monte Carlo paths |
| Consecutive losing trades | 9 | **Warn** | 1-in-20 Monte Carlo streak |
| Consecutive losing trades | 13 | **Halt** | Beyond 99% of Monte Carlo streaks |
| Any single trade loss | worse than -8.8% | **Halt** | 1.5× the worst backtest trade. Means stops or fills are failing |
| Average losing trade (after ≥ 8 losers) | worse than -5.2% | **Warn**: review | 1.5× backtest average loss. The payoff edge is eroding |
| Wins after 10 / 15 / 20 / 30 trades | ≤ 1 / 2 / 4 / 7 | **Warn**: review | Less than a 5% chance at the backtest win rate (42%) |

A halt is cleared only by you (`python bot.py --reset-halt`) after reviewing what happened.

## 6. What could still go wrong (read before signing)

- **The backtest is optimistic.** Some design choices were made with 2023+ results visible. Walk-forward 2022 → now (parameters re-chosen yearly from past data only): CAGR +7.8%, Sharpe 0.55, max DD -26.8%. Expect live results closer to the walk-forward than the headline.
- **It's a small sample:** 36 trades in about 6 years (around 6 a year). Months can pass with no trade, or with only small losses.
- **Bear markets:** on Alpaca the bot holds cash, so it protects capital but doesn't profit. It can't short here.
- **It trails BTC in strong bull runs** (2020: +33.9% vs BTC +203.1%). Its purpose is to stay invested through trends and out of crashes.
- **Execution:** signals use Binance data, while fills happen on Alpaca BTC/USD. Exchange outages or gaps can fill stops worse than planned. The single-loss limit exists for that.

## 7. Sign-off

No approval on file. The bot runs dry only.

To approve exactly this version (fingerprint `ea6baed8d9d9`):

```
python signoff.py --approve "Your Name"
```
