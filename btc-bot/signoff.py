#!/usr/bin/env python3
"""
Strategy sign-off pack.

  python signoff.py                      build reports/SIGNOFF.md + reports/survival_limits.json
  python signoff.py --approve "Name"     record your approval of THIS exact parameter set (SIGNED_OFF.json)

The bot refuses to place orders unless SIGNED_OFF.json matches the strategy's current fingerprint.
Change any parameter and the fingerprint changes, so the strategy needs a fresh sign-off.

The survival limits are fixed here, before any live trading, from the backtest and Monte Carlo
distributions. Live results are judged against these numbers, not against hindsight.
"""
import argparse, datetime as dt, json, math, os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from btc_bot import data, features, strategy, backtest as bt

OUT = os.path.join(HERE, "reports")
SIGNED = os.path.join(HERE, "SIGNED_OFF.json")
VENUE = "spot_long_flat"                    # what the Alpaca bot runs
P = {**strategy.DEFAULTS, **strategy.PROFILES[VENUE]}
COSTS = bt.Costs(25, 5)
IS, OOS, FULL = slice("2020-06-01", "2022-12-31"), slice("2023-01-01", None), slice("2020-06-01", None)


def pct(x, plus=True): return (f"{x:+.1%}" if plus else f"{x:.1%}") if x == x else "n/a"


def max_streak(losses):
    m = c = 0
    for v in losses:
        c = c + 1 if v else 0; m = max(m, c)
    return m


def underwater_days(eq):
    peak, longest, start = -np.inf, 0, None
    d = eq.resample("1D").last().dropna()
    for t, v in d.items():
        if v >= peak: peak, start = v, t
        else: longest = max(longest, (t - start).days)
    return longest


def block_mc_dd(eq, n=10000, block=20, seed=11):
    r = eq.resample("1D").last().pct_change().dropna().to_numpy()
    rng, out = np.random.default_rng(seed), np.empty(n)
    for k in range(n):
        st = rng.integers(0, len(r) - block, len(r) // block + 1)
        s = np.concatenate([r[i:i + block] for i in st])[:len(r)]
        e = np.cumprod(1 + s); out[k] = (e / np.maximum.accumulate(e) - 1).min()
    return out


def binom_cdf(k, n, p):
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def binom_low(n, p, q=0.05):
    """Largest win count k such that P(X <= k) <= q, i.e. below it is 'unusually bad luck'."""
    k = -1
    while binom_cdf(k + 1, n, p) <= q: k += 1
    return k


def describe_rules(p):
    return f"""**Data:** Binance BTCUSDT perpetual and spot 1h klines (taker buy volume), aggregated into **{p['timeframe']} bars**. Decisions are made only on closed bars.

**Flow signal:** `delta = taker_buy − taker_sell` per bar. `flow_imb = Σdelta / Σvolume` over the last **{p['flow_n']} bars ({p['flow_n'] * 4 // 24} days)**.
`flow_dev = flow_imb − median(flow_imb over the last {p['base_n']} bars ≈ 1 year)`. The same is computed on spot (`spot_dev`).

**Regime** (each bar):
- **BULL**: close > EMA({p['ema_slow']}) and ADX({p.get('adx_n', 14)}) ≥ {p['chop_adx_min']}.
- **BEAR**: close < EMA({p['ema_slow']}), EMA falling over {p.get('slope_n', 30)} bars, ADX ≥ {p['chop_adx_min']}. On this venue (Alpaca spot): **hold cash**.
- **RANGE**: everything else. **Pause**: no new entries.

**Entry (long):** regime is BULL **and** `flow_dev > {p['entry_dev']}` **and** `spot_dev > 0`. Buy at the next bar's open.

**Position size:** `fraction = {p['target_vol']:.0%} ÷ BTC's annualised volatility (last {p['z_n']} bars)`, capped at **100% of equity** (no leverage).
Example: BTC running at 60% volatility → 50% of equity.

**Exits** (whichever comes first):
1. **Flow exit:** `flow_dev < {p['exit_dev']}` at a bar close → sell at the next open.
2. **Initial stop:** entry − **{p['stop_atr']} × ATR({p['atr_n']})**. Rests on the exchange as a stop-limit order.
3. **Trailing stop:** highest close since entry − **{p['trail_atr']} × ATR**, ratchets up only.

Consolidation never forces an exit. Testing showed that exiting on consolidation hurts (REPORT.md §4).

**Not used** (tested, no benefit): funding-rate veto, shorting on spot, tight stops, spot-only flow."""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--approve", metavar="NAME", help="record sign-off of the current parameter set")
    a = ap.parse_args()
    fp = strategy.fingerprint(P)
    os.makedirs(OUT, exist_ok=True)

    df = features.resample(data.load_dataset(), P["timeframe"])
    f = features.build(df, P)
    run = lambda sl, p=P: bt.run(f.loc[sl], strategy.signal(f.loc[sl], p), p, costs=COSTS)
    R = {k: run(sl) for k, sl in [("IS", IS), ("OOS", OOS), ("FULL", FULL)]}
    t = R["FULL"].trades.copy(); eq = R["FULL"].equity
    t["year"] = t.entry_time.dt.year
    is_t, oos_t = t[t.entry_time < "2023-01-01"], t[t.entry_time >= "2023-01-01"]
    win = (t.pnl > 0).mean()
    loss_r, win_r = t.ret[t.ret <= 0], t.ret[t.ret > 0]

    # --- Monte Carlo
    rng = np.random.default_rng(5)
    R_ = t.ret.to_numpy()
    streaks, wins30, fin = [], [], []
    for _ in range(20000):
        x = rng.choice(R_, 30)
        streaks.append(max_streak(x <= 0)); wins30.append((x > 0).mean()); fin.append(np.prod(1 + x))
    streaks, wins30, fin = map(np.array, (streaks, wins30, fin))
    mcdd = block_mc_dd(eq)
    dd_bt = R["FULL"].stats["max_dd"]

    # --- survival limits (pre-registered)
    limits = dict(
        version=strategy.VERSION, fingerprint=fp, venue=VENUE,
        dd_warn=round(min(dd_bt, -0.05) - 0.01, 2),               # beyond the worst backtest drawdown
        dd_halt=round(float(np.percentile(mcdd, 5)), 2),          # beyond 95% of Monte Carlo paths
        warn_consecutive_losses=int(np.percentile(streaks, 95)),
        max_consecutive_losses=int(np.percentile(streaks, 99)) + 1,
        worst_single_loss=round(1.5 * float(t.ret.min()), 3),     # a loss this big = stops/execution failing
        avg_loss_review=round(1.5 * float(loss_r.mean()), 3),     # after >= 8 losing trades
        win_rate_review={str(n): binom_low(n, win) for n in (10, 15, 20, 30)},  # wins at/below this after n trades
    )
    json.dump(limits, open(os.path.join(OUT, "survival_limits.json"), "w"), indent=2)

    md = []; add = md.append
    add(f"# Strategy sign-off: BTC Flow Regime v{strategy.VERSION}\n")
    add(f"Fingerprint **`{fp}`** · venue **Alpaca BTC/USD spot (long or cash)** · generated {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC · "
        f"data {f.index[0]:%Y-%m-%d} → {f.index[-1]:%Y-%m-%d} · costs 0.25% fee + 0.05% slippage per side\n")
    add("> Educational research, not financial advice. Backtests are estimates. This document fixes, **before** live trading, "
        "what results count as \"the strategy is behaving as tested\" and what counts as \"it has stopped working\".\n")
    add("## 1. The rules you are signing off\n")
    add(describe_rules(P))
    add("\n<details><summary>Every parameter (this exact set produces the fingerprint)</summary>\n")
    add("| Parameter | Value |\n|---|---|")
    for k, v in P.items(): add(f"| `{k}` | {v} |")
    add("\n</details>\n")
    add("Code: `btc_bot/strategy.py` (rules), `btc_bot/features.py` (indicators), `btc_bot/backtest.py` (simulation), `bot.py` (execution).\n")

    add("## 2. Win rate: what to expect, and why it survives\n")
    add(f"**The win rate is low by design: {win:.0%} overall ({(is_t.pnl > 0).mean():.0%} in-sample, {(oos_t.pnl > 0).mean():.0%} out-of-sample).** "
        "The strategy survives on its **payoff ratio**, not its win rate. Losses are capped by the stop and the flow exit. Winners ride "
        "multi-week trends.\n")
    add("| | Trades | Win rate | Avg win | Avg loss | Payoff (avg win ÷ avg loss) | Expectancy per trade | Worst trade |\n|---|---|---|---|---|---|---|---|")
    for lab, x in [("In-sample 2020-06 → 2022", is_t), ("**Out-of-sample 2023 → now**", oos_t), ("Full", t)]:
        w, l = x.ret[x.ret > 0], x.ret[x.ret <= 0]
        add(f"| {lab} | {len(x)} | {(x.pnl > 0).mean():.0%} | {pct(w.mean())} | {pct(l.mean())} | {w.mean() / -l.mean():.1f}× | {pct(x.ret.mean())} | {pct(x.ret.min())} |")
    add("\nThe number that held from in-sample to out-of-sample is the **average loss**. The win rate moved "
        "(it was *higher* out-of-sample). The win rate swings a lot year to year, so don't judge the strategy on a handful of trades:\n")
    add("| Year | Trades | Win rate | Avg win | Avg loss | Sum of trade returns |\n|---|---|---|---|---|---|")
    for y, g in t.groupby("year"):
        w, l = g.ret[g.ret > 0], g.ret[g.ret <= 0]
        add(f"| {y} | {len(g)} | {(g.pnl > 0).mean():.0%} | {pct(w.mean()) if len(w) else '–'} | {pct(l.mean()) if len(l) else '–'} | {pct(g.ret.sum())} |")
    add(f"\nMonte Carlo (20,000 random re-orderings of the backtest trades, 30 trades each, ≈ 4–5 years of trading): "
        f"win rate 90% band **{np.percentile(wins30, 5):.0%} – {np.percentile(wins30, 95):.0%}**. Longest losing streak: median "
        f"{int(np.median(streaks))}, 1-in-20 **{int(np.percentile(streaks, 95))}**, 1-in-100 {int(np.percentile(streaks, 99))} "
        f"(backtest actual: {max_streak((t.pnl <= 0).to_numpy())}). P(30 trades lose money overall): {(fin < 1).mean():.1%}.\n")
    add("**Why earlier strategies often fail on win rate:** high-win-rate systems usually take small, frequent profits and "
        "rare large losses. They look great until the large loss arrives, and then the drawdown doesn't survive. This one is built the "
        "other way round: frequent small, capped losses and rare large wins. Its failure mode is slow (a long string of small losses), "
        "which the limits in §5 catch early.\n")

    add("## 3. Drawdown: what to expect, and how bad it can get\n")
    add("| Measure | Value |\n|---|---|")
    add(f"| Backtest max drawdown, in-sample | {pct(R['IS'].stats['max_dd'])} |")
    add(f"| Backtest max drawdown, **out-of-sample** | {pct(R['OOS'].stats['max_dd'])} |")
    add(f"| Backtest max drawdown, full period | {pct(dd_bt)} |")
    add(f"| Buy-and-hold BTC max drawdown, same period | {pct(bt.buy_and_hold(f.loc[FULL]).stats['max_dd'])} |")
    add(f"| Monte Carlo max drawdown (daily returns resampled in 20-day blocks, 10,000 paths): typical | {pct(np.percentile(mcdd, 50))} |")
    add(f"| Monte Carlo: 1-in-20 bad path | **{pct(np.percentile(mcdd, 5))}** |")
    add(f"| Monte Carlo: 1-in-100 bad path | {pct(np.percentile(mcdd, 1))} |")
    add(f"| Longest time below a previous equity high | {underwater_days(eq)} days |")
    add(f"\n**Plan for a {pct(np.percentile(mcdd, 5))} drawdown, not {pct(dd_bt)}.** The backtest is one path; the Monte Carlo shows "
        "how unlucky ordering of the same kind of returns can hurt. Only trade a size where that number is survivable for you.\n")
    add("Drawdown by year (strategy vs buy-and-hold):\n")
    bh = bt.buy_and_hold(f.loc[FULL]).equity
    add("| Year | Strategy return | Strategy max DD | BTC return | BTC max DD |\n|---|---|---|---|---|")
    for y in sorted(set(eq.index.year)):
        e, b = eq[eq.index.year == y], bh[bh.index.year == y]
        add(f"| {y} | {pct(e.iloc[-1] / e.iloc[0] - 1)} | {pct((e / e.cummax() - 1).min())} | {pct(b.iloc[-1] / b.iloc[0] - 1)} | {pct((b / b.cummax() - 1).min())} |")

    add("\n## 4. Position size is the drawdown dial\n")
    add("The rules are the same; only `target_vol` changes. Drawdown scales with size, while Sharpe barely moves. v1.0 uses **30%**.\n")
    add("| target_vol | CAGR (full) | Sharpe IS / OOS | Max DD (full) | Monte Carlo 1-in-20 DD |\n|---|---|---|---|---|")
    for tv in (0.2, 0.3, 0.4, 0.5):
        p = {**P, "target_vol": tv}
        r_is, r_oos, r_f = run(IS, p), run(OOS, p), run(FULL, p)
        add(f"| {tv:.0%}{' ← v1.0' if tv == P['target_vol'] else ''} | {pct(r_f.stats['cagr'])} | {r_is.stats['sharpe']:.2f} / {r_oos.stats['sharpe']:.2f} | "
            f"{pct(r_f.stats['max_dd'])} | {pct(np.percentile(block_mc_dd(r_f.equity, n=3000), 5))} |")

    add("\n## 5. Survival limits (fixed now, enforced by the bot)\n")
    add("These are written to `reports/survival_limits.json`. On sign-off they're copied into `SIGNED_OFF.json`, which the bot reads.\n")
    add("| Check | Limit | Bot action | Why this number |\n|---|---|---|---|")
    add(f"| Drawdown from equity peak | {pct(limits['dd_warn'])} | **Warn** in log/console | Beyond the worst backtest drawdown ({pct(dd_bt)}) |")
    add(f"| Drawdown from equity peak | {pct(limits['dd_halt'])} | **Halt**: flatten, stop trading | Beyond 95% of Monte Carlo paths |")
    add(f"| Consecutive losing trades | {limits['warn_consecutive_losses']} | **Warn** | 1-in-20 Monte Carlo streak |")
    add(f"| Consecutive losing trades | {limits['max_consecutive_losses']} | **Halt** | Beyond 99% of Monte Carlo streaks |")
    add(f"| Any single trade loss | worse than {pct(limits['worst_single_loss'])} | **Halt** | 1.5× the worst backtest trade. Means stops or fills are failing |")
    add(f"| Average losing trade (after ≥ 8 losers) | worse than {pct(limits['avg_loss_review'])} | **Warn**: review | 1.5× backtest average loss. The payoff edge is eroding |")
    wr = limits["win_rate_review"]
    add(f"| Wins after 10 / 15 / 20 / 30 trades | ≤ {wr['10']} / {wr['15']} / {wr['20']} / {wr['30']} | **Warn**: review | Less than a 5% chance at the backtest win rate ({win:.0%}) |")
    add("\nA halt is cleared only by you (`python bot.py --reset-halt`) after reviewing what happened.\n")

    wf_note = ""
    try:
        import research
        fc = research.FeatCache(df)
        wf_eq, _ = research.walk_forward(fc, list(range(2022, dt.date.today().year + 1)), VENUE)
        s = bt.stats(wf_eq, pd.DataFrame())
        wf_note = f" Walk-forward 2022 → now (parameters re-chosen yearly from past data only): CAGR {pct(s['cagr'])}, Sharpe {s['sharpe']:.2f}, max DD {pct(s['max_dd'])}."
    except Exception as e:  # pragma: no cover
        wf_note = f" (walk-forward unavailable: {e})"
    add("## 6. What could still go wrong (read before signing)\n")
    add(f"- **The backtest is optimistic.** Some design choices were made with 2023+ results visible.{wf_note} Expect live results "
        "closer to the walk-forward than the headline.\n"
        f"- **It's a small sample:** {len(t)} trades in about 6 years (around 6 a year). Months can pass with no trade, or with only small losses.\n"
        "- **Bear markets:** on Alpaca the bot holds cash, so it protects capital but doesn't profit. It can't short here.\n"
        f"- **It trails BTC in strong bull runs** (2020: {pct(eq[eq.index.year == 2020].iloc[-1] / eq[eq.index.year == 2020].iloc[0] - 1)} "
        f"vs BTC {pct(bh[bh.index.year == 2020].iloc[-1] / bh[bh.index.year == 2020].iloc[0] - 1)}). Its purpose is to stay invested through trends and out of crashes.\n"
        "- **Execution:** signals use Binance data, while fills happen on Alpaca BTC/USD. Exchange outages or gaps can fill stops worse than planned. The single-loss limit exists for that.\n")
    add("## 7. Sign-off\n")
    if os.path.exists(SIGNED):
        sgn = json.load(open(SIGNED))
        ok = sgn.get("fingerprint") == fp
        add(f"Current approval on file: **{sgn['name']}**, {sgn['date']}, fingerprint `{sgn['fingerprint']}` → "
            + ("**matches this strategy.**" if ok else "**does NOT match: parameters changed since sign-off.**"))
    else:
        add("No approval on file. The bot runs dry only.")
    add(f"\nTo approve exactly this version (fingerprint `{fp}`):\n\n```\npython signoff.py --approve \"Your Name\"\n```\n")

    with open(os.path.join(OUT, "SIGNOFF.md"), "w") as fh: fh.write("\n".join(md))
    print("\n".join(md)); print(f"\nWrote {OUT}/SIGNOFF.md and survival_limits.json")

    if a.approve:
        rec = dict(name=a.approve, date=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                   version=strategy.VERSION, fingerprint=fp, params=P, limits=limits)
        json.dump(rec, open(SIGNED, "w"), indent=2, default=str)
        print(f"\nApproved by {a.approve}: fingerprint {fp} written to SIGNED_OFF.json")


if __name__ == "__main__":
    main()
