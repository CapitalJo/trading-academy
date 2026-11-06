# Strategy sign-off: S2, Flow Regime v1.1: v1.0 + take 1/3 profit at +2 ATR, rest to breakeven (v1.1)

Strategy **S2** · fingerprint **`e25db8f91eee`** · venue **Alpaca BTC/USD spot (long or cash)** · generated 2026-11-06 04:47 UTC · data 2019-12-31 → 2026-11-04 · costs 0.25% fee + 0.05% slippage per side

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

4. **Partial profit-take:** when a bar closes at entry + **2.0 × ATR** or higher, sell **33%** of the position at the next open (once per trade). The stop on the rest moves up to the entry price (breakeven) or higher.

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
| `tp_atr` | 2.0 |
| `tp_frac` | 0.33 |

</details>

Code: `btc_bot/strategy.py` (rules), `btc_bot/features.py` (indicators), `btc_bot/backtest.py` (simulation), `bot.py` (execution).

## 2. Win rate: what to expect, and why it survives

**Win rate 65% overall (67% in-sample, 65% out-of-sample).** The early partial profit turns many trades that used to give back their gains into small wins. Losses are still capped by the stop and the flow exit, and the rest of each position still rides multi-week trends. The trade-off is a smaller payoff per win.

| | Trades | Win rate | Avg win | Avg loss | Payoff (avg win ÷ avg loss) | Expectancy per trade | Worst trade |
|---|---|---|---|---|---|---|---|
| In-sample 2020-06 → 2022 | 15 | 67% | +5.2% | -3.9% | 1.3× | +2.1% | -4.5% |
| **Out-of-sample 2023 → now** | 31 | 65% | +5.4% | -3.5% | 1.5× | +2.2% | -5.9% |
| Full | 46 | 65% | +5.3% | -3.6% | 1.5× | +2.2% | -5.9% |

The number that held from in-sample to out-of-sample is the **average loss**. The win rate moved (it was *higher* out-of-sample). The win rate swings a lot year to year, so don't judge the strategy on a handful of trades:

| Year | Trades | Win rate | Avg win | Avg loss | Sum of trade returns |
|---|---|---|---|---|---|
| 2020 | 3 | 67% | +12.9% | -3.9% | +22.0% |
| 2021 | 4 | 50% | +10.8% | -3.7% | +14.2% |
| 2022 | 8 | 75% | +0.7% | -4.1% | -3.9% |
| 2023 | 5 | 60% | +8.6% | -3.5% | +18.8% |
| 2024 | 12 | 75% | +5.2% | -3.9% | +34.9% |
| 2025 | 4 | 50% | +4.7% | -3.6% | +2.1% |
| 2026 | 10 | 60% | +4.3% | -3.1% | +13.6% |

Monte Carlo (20,000 random re-orderings of the backtest trades, 30 trades each, ≈ 4–5 years of trading): win rate 90% band **50% – 80%**. Longest losing streak: median 3, 1-in-20 **5**, 1-in-100 6 (backtest actual: 2). P(30 trades lose money overall): 5.0%.

**Why earlier strategies often fail on win rate:** high-win-rate systems usually take small, frequent profits and rare large losses. They look great until the large loss arrives, and then the drawdown doesn't survive. This one keeps the loss side capped (worst trade -5.9%, average loss -3.6%) while banking part of each winner early, so its high win rate doesn't depend on avoiding a rare big loss. Its failure mode is slow (a long string of small losses), which the limits in §5 catch early.

## 3. Drawdown: what to expect, and how bad it can get

| Measure | Value |
|---|---|
| Backtest max drawdown, in-sample | -10.6% |
| Backtest max drawdown, **out-of-sample** | -13.9% |
| Backtest max drawdown, full period | -13.9% |
| Buy-and-hold BTC max drawdown, same period | -77.1% |
| Monte Carlo max drawdown (daily returns resampled in 20-day blocks, 10,000 paths): typical | -16.5% |
| Monte Carlo: 1-in-20 bad path | **-27.6%** |
| Monte Carlo: 1-in-100 bad path | -34.2% |
| Longest time below a previous equity high | 455 days |

**Plan for a -27.6% drawdown, not -13.9%.** The backtest is one path; the Monte Carlo shows how unlucky ordering of the same kind of returns can hurt. Only trade a size where that number is survivable for you.

Drawdown by year (strategy vs buy-and-hold):

| Year | Strategy return | Strategy max DD | BTC return | BTC max DD |
|---|---|---|---|---|
| 2020 | +22.5% | -6.5% | +203.1% | -20.0% |
| 2021 | +13.8% | -7.2% | +57.7% | -54.2% |
| 2022 | -4.0% | -10.6% | -64.7% | -67.2% |
| 2023 | +18.0% | -6.8% | +156.0% | -21.3% |
| 2024 | +35.1% | -11.7% | +120.8% | -30.1% |
| 2025 | +1.6% | -9.5% | -6.6% | -34.4% |
| 2026 | +12.4% | -10.3% | -8.4% | -40.0% |

## 4. Position size is the drawdown dial

The rules are the same; only `target_vol` changes. Drawdown scales with size, while Sharpe barely moves. v1.0 uses **30%**.

| target_vol | CAGR (full) | Sharpe IS / OOS | Max DD (full) | Monte Carlo 1-in-20 DD |
|---|---|---|---|---|
| 20% | +10.9% | 1.04 / 1.24 | -10.0% | -19.3% |
| 30% ← v1.0 | +14.9% | 1.04 / 1.19 | -13.9% | -27.3% |
| 40% | +18.1% | 1.05 / 1.14 | -15.9% | -33.6% |
| 50% | +20.5% | 1.08 / 1.13 | -17.9% | -37.2% |

## 5. Survival limits (fixed now, enforced by the bot)

These are written to `reports/survival_limits_S2.json`. On sign-off they're copied into `SIGNED_OFF_S2.json`, which the bot reads.

| Check | Limit | Bot action | Why this number |
|---|---|---|---|
| Drawdown from equity peak | -15.0% | **Warn** in log/console | Beyond the worst backtest drawdown (-13.9%) |
| Drawdown from equity peak | -28.0% | **Halt**: flatten, stop trading | Beyond 95% of Monte Carlo paths |
| Consecutive losing trades | 5 | **Warn** | 1-in-20 Monte Carlo streak |
| Consecutive losing trades | 7 | **Halt** | Beyond 99% of Monte Carlo streaks |
| Any single trade loss | worse than -8.8% | **Halt** | 1.5× the worst backtest trade. Means stops or fills are failing |
| Average losing trade (after ≥ 8 losers) | worse than -5.4% | **Warn**: review | 1.5× backtest average loss. The payoff edge is eroding |
| Wins after 10 / 15 / 20 / 30 trades | ≤ 3 / 6 / 8 / 14 | **Warn**: review | Less than a 5% chance at the backtest win rate (65%) |

A halt is cleared only by you (`python bot.py --reset-halt`) after reviewing what happened.

## 6. What could still go wrong (read before signing)

- **The backtest is optimistic.** Some design choices were made with 2023+ results visible. Walk-forward 2022 → now (parameters re-chosen yearly from past data only): CAGR +3.2%, Sharpe 0.28, max DD -27.5%. Expect live results closer to the walk-forward than the headline.
- **It's a small sample:** 46 trades in about 6 years (around 6 a year). Months can pass with no trade, or with only small losses.
- **Bear markets:** on Alpaca the bot holds cash, so it protects capital but doesn't profit. It can't short here.
- **It trails BTC in strong bull runs** (2020: +22.5% vs BTC +203.1%). Its purpose is to stay invested through trends and out of crashes.
- **Execution:** signals use Binance data, while fills happen on Alpaca BTC/USD. Exchange outages or gaps can fill stops worse than planned. The single-loss limit exists for that.

## 7. Sign-off

No approval on file. The bot runs dry only.

To approve exactly this version (fingerprint `e25db8f91eee`):

```
python signoff.py --strategy S2 --approve "Your Name"
```
