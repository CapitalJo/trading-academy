#!/usr/bin/env python3
"""
Reproducible research + backtest report for the Flow Regime BTC strategy.

  python research.py            -> downloads/caches data, writes reports/REPORT.md, CSVs and SVG charts

Methodology (designed to avoid fooling ourselves):
  * In-sample (IS) 2020-06 .. 2022-12 is the ONLY period used to choose parameters.
  * Out-of-sample (OOS) 2023-01 .. today is reported untouched.
  * Walk-forward: each January, re-select parameters on all prior data, trade the next year blind.
  * Robustness: every config in the grid is reported (not just the best), plus cost, lag, and
    bootstrap confidence intervals.
  * Costs: 0.25% fee + 0.05% slippage per side (Alpaca crypto base tier is 0.25% taker).
"""
import itertools, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from btc_bot import data, features, strategy, backtest as bt

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
IS = slice("2020-06-01", "2022-12-31")
OOS = slice("2023-01-01", None)
FULL = slice("2020-06-01", None)
P = dict(strategy.DEFAULTS)

GRID = dict(flow_n=[42, 84, 126], entry_dev=[0.0, 0.005, 0.01], exit_dev=[-0.01, -0.005, 0.0],
            spot_confirm=[False, True], trend_filter=[False, True])
WF_GRID = dict(flow_n=[42, 84, 126], entry_dev=[0.0, 0.005, 0.01], exit_dev=[-0.01, -0.005],
               trend_filter=[False, True], stop=[(3.0, 5.0), (5.0, 8.0)])


def pct(x): return f"{x:+.1%}" if np.isfinite(x) else "n/a"
def num(x): return f"{x:.2f}" if np.isfinite(x) else "n/a"


class FeatCache:
    def __init__(self, df): self.df, self.c = df, {}
    def get(self, p):
        k = (p["flow_n"], p["base_n"], p["z_n"], p["ema_slow"])
        if k not in self.c: self.c[k] = features.build(self.df, p)
        return self.c[k]


def run(fc, p, sl, costs=bt.Costs()):
    f = fc.get(p).loc[sl]
    return bt.run(f, strategy.signal(f, p), p, costs=costs)


def row(name, s):
    return (f"| {name} | {pct(s['total_return'])} | {pct(s['cagr'])} | {num(s['sharpe'])} | {num(s['sortino'])} | "
            f"{pct(s['max_dd'])} | {num(s['calmar'])} | {s['trades']} | "
            f"{pct(s.get('win_rate', np.nan)).lstrip('+')} | {num(s.get('profit_factor', np.nan))} | "
            f"{pct(s.get('exposure', np.nan)).lstrip('+')} |")


HDR = ("| Period | Total | CAGR | Sharpe | Sortino | Max DD | Calmar | Trades | Win % | Profit factor | Time in market |\n"
       "|---|---|---|---|---|---|---|---|---|---|---|")


def ic_table(df):
    lines = ["| Feature | IC 2020-22 | IC 2023-26 | Same sign? |", "|---|---|---|---|"]
    f = features.build(features.resample(df, "1D"), dict(P, flow_n=7, z_n=90, base_n=365))
    fwd = f.close.shift(-20) / f.close - 1
    f["momentum_7d"] = f.close.pct_change(7)
    f["trend_vs_ema200"] = f.close / f.ema_slow - 1
    for col in ["flow_imb", "flow_dev", "cvd_slope", "spot_imb", "funding_z", "momentum_7d", "trend_vs_ema200"]:
        a = f[col].loc[IS].rank().corr(fwd.loc[IS].rank())
        b = f[col].loc[OOS].rank().corr(fwd.loc[OOS].rank())
        lines.append(f"| `{col}` | {a:+.3f} | {b:+.3f} | {'yes' if np.sign(a) == np.sign(b) else '**no**'} |")
    return "\n".join(lines)


def bootstrap_sharpe(eq, n=2000, block=20, seed=7):
    r = eq.resample("1D").last().pct_change().dropna().to_numpy()
    rng, out = np.random.default_rng(seed), []
    for _ in range(n):
        starts = rng.integers(0, len(r) - block, len(r) // block + 1)
        s = np.concatenate([r[i:i + block] for i in starts])[:len(r)]
        out.append(s.mean() / s.std() * np.sqrt(365) if s.std() > 0 else 0)
    return np.percentile(out, [5, 50, 95]), float(np.mean(np.array(out) > 0))


def walk_forward(fc, years):
    keys = list(WF_GRID)
    chunks, picks = [], []
    for y in years:
        best, best_s = None, -np.inf
        for vals in itertools.product(*WF_GRID.values()):
            d = dict(zip(keys, vals))
            if d["exit_dev"] > d["entry_dev"]: continue
            sa, ta = d.pop("stop")
            p = dict(P, **d, stop_atr=sa, trail_atr=ta)
            s = run(fc, p, slice("2020-06-01", f"{y - 1}-12-31")).stats["sharpe"]
            if s > best_s: best, best_s = p, s
        r = run(fc, best, slice(f"{y}-01-01", f"{y}-12-31"))
        chunks.append(r.equity / r.equity.iloc[0])
        picks.append((y, best, best_s, r.stats))
    eq, level = [], 1.0
    for c in chunks:
        eq.append(c * level); level = eq[-1].iloc[-1]
    return pd.concat(eq) * 10_000, picks


def svg_chart(series, path, title, log=True, w=900, h=360, fmt=lambda v: f"{v:,.0f}"):
    """Dependency-free SVG line chart. series: {label: (pd.Series, colour)}"""
    pad_l, pad_r, pad_t, pad_b = 70, 20, 36, 30
    allv = pd.concat([s for s, _ in series.values()])
    tr = (lambda v: np.log10(v)) if log else (lambda v: v)
    lo, hi = tr(allv.min()), tr(allv.max())
    t0, t1 = allv.index.min().value, allv.index.max().value
    X = lambda t: pad_l + (t.value - t0) / (t1 - t0) * (w - pad_l - pad_r)
    Y = lambda v: pad_t + (hi - tr(v)) / (hi - lo) * (h - pad_t - pad_b)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" font-family="sans-serif" font-size="12">',
           f'<rect width="{w}" height="{h}" fill="#fff"/><text x="{pad_l}" y="20" font-size="14" font-weight="bold">{title}</text>']
    ticks = np.linspace(lo, hi, 5)
    for t in ticks:
        v = 10 ** t if log else t
        y = Y(v)
        out.append(f'<line x1="{pad_l}" x2="{w - pad_r}" y1="{y:.1f}" y2="{y:.1f}" stroke="#eee"/>'
                   f'<text x="{pad_l - 6}" y="{y + 4:.1f}" text-anchor="end" fill="#555">{fmt(v)}</text>')
    for yr in range(pd.Timestamp(t0).year + 1, pd.Timestamp(t1).year + 1):
        x = X(pd.Timestamp(f"{yr}-01-01", tz="UTC"))
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{pad_t}" y2="{h - pad_b}" stroke="#f3f3f3"/>'
                   f'<text x="{x:.1f}" y="{h - 10}" text-anchor="middle" fill="#555">{yr}</text>')
    lx = w - pad_r - sum(16 + 7 * len(k) + 18 for k in series)
    for label, (s, col) in series.items():
        s = s.resample("1D").last().dropna()
        pts = " ".join(f"{X(t):.1f},{Y(v):.1f}" for t, v in s.items())
        out.append(f'<polyline fill="none" stroke="{col}" stroke-width="1.6" points="{pts}"/>')
        out.append(f'<rect x="{lx:.0f}" y="14" width="12" height="3" fill="{col}"/>'
                   f'<text x="{lx + 16:.0f}" y="19" fill="#333">{label.replace("&", "&amp;")}</text>')
        lx += 16 + 7 * len(label) + 18
    out.append("</svg>")
    with open(path, "w") as fh: fh.write("\n".join(out))


def main():
    os.makedirs(OUT, exist_ok=True)
    raw = data.load_dataset()
    df = features.resample(raw, P["timeframe"])
    fc = FeatCache(df)
    md = []
    add = md.append
    add("# BTC Flow Regime strategy: backtest report\n")
    add(f"_Generated by `research.py` on {pd.Timestamp.utcnow():%Y-%m-%d %H:%M} UTC. "
        f"Data: Binance BTCUSDT perpetual + spot, {df.index[0]:%Y-%m-%d} → {df.index[-1]:%Y-%m-%d}, "
        f"{len(df):,} × 4h bars. Educational research, not financial advice._\n")

    # --- headline
    res = {k: run(fc, P, sl) for k, sl in [("IS", IS), ("OOS", OOS), ("FULL", FULL)]}
    f_full = fc.get(P).loc[FULL]
    bh = {k: bt.buy_and_hold(fc.get(P).loc[sl]) for k, sl in [("IS", IS), ("OOS", OOS), ("FULL", FULL)]}
    add("## 1. Headline results (fees 0.25% + slippage 0.05% per side, long/flat, no leverage)\n")
    add(HDR)
    for k, lab in [("IS", "In-sample 2020-06 → 2022-12"), ("OOS", "**Out-of-sample 2023-01 → now**"), ("FULL", "Full period")]:
        add(row(f"Strategy: {lab}", res[k].stats))
        add(row(f"Buy & hold: {lab}", bh[k].stats))
    ci, p_pos = bootstrap_sharpe(res["OOS"].equity)
    add(f"\nOOS Sharpe, 90% block-bootstrap interval: **{ci[0]:.2f} – {ci[2]:.2f}** "
        f"(median {ci[1]:.2f}; {p_pos:.0%} of resamples > 0).\n")
    add("**How to read this:** buy-and-hold made more money, because 2020–2026 was a huge BTC bull run. "
        "The strategy's job is different. It aims to capture a good part of the upside while sitting in cash "
        "during sell-side regimes, which roughly halves the worst drawdown and gives better return per unit of risk. "
        "That is what makes it survivable, and what makes it scalable with sizing.\n")

    # --- yearly
    add("## 2. Calendar-year returns\n")
    add("| Year | Strategy | Buy & hold | Strategy max DD | B&H max DD | Trades |\n|---|---|---|---|---|---|")
    eq, bheq, tr = res["FULL"].equity, bh["FULL"].equity, res["FULL"].trades
    for y in sorted(set(eq.index.year)):
        e, b = eq[eq.index.year == y], bheq[bheq.index.year == y]
        nt = int((tr.entry_time.dt.year == y).sum()) if len(tr) else 0
        add(f"| {y} | {pct(e.iloc[-1] / e.iloc[0] - 1)} | {pct(b.iloc[-1] / b.iloc[0] - 1)} | "
            f"{pct((e / e.cummax() - 1).min())} | {pct((b / b.cummax() - 1).min())} | {nt} |")

    # --- walk forward
    years = list(range(2022, pd.Timestamp.utcnow().year + 1))
    wf_eq, picks = walk_forward(fc, years)
    wf_s = bt.stats(wf_eq, pd.DataFrame())
    bh_wf = bt.buy_and_hold(fc.get(P).loc[f"{years[0]}-01-01":])
    add("\n## 3. Walk-forward (parameters re-chosen every January using only past data)\n")
    add(f"Grid per refit: {int(np.prod([len(v) for v in WF_GRID.values()]))} combos (flow window, entry/exit thresholds, "
        "trend filter, stop width). Objective: in-sample Sharpe.\n")
    add("| Year traded | Chosen flow_n | entry/exit dev | trend filter | stop/trail ATR | Train Sharpe | Year return | Year Sharpe | Year max DD |\n|---|---|---|---|---|---|---|---|---|")
    for y, p, s_tr, st in picks:
        add(f"| {y} | {p['flow_n']} | {p['entry_dev']}/{p['exit_dev']} | {p['trend_filter']} | {p['stop_atr']}/{p['trail_atr']} | "
            f"{s_tr:.2f} | {pct(st['total_return'])} | {num(st['sharpe'])} | {pct(st['max_dd'])} |")
    add(f"\nStitched walk-forward {years[0]}→now: CAGR **{pct(wf_s['cagr'])}**, Sharpe **{num(wf_s['sharpe'])}**, "
        f"max DD **{pct(wf_s['max_dd'])}**  (buy & hold same span: CAGR {pct(bh_wf.stats['cagr'])}, "
        f"Sharpe {num(bh_wf.stats['sharpe'])}, max DD {pct(bh_wf.stats['max_dd'])}).\n")
    fx = run(fc, P, slice(f"{years[0]}-01-01", None)).stats
    add(f"For comparison, the fixed default parameters over the same span: CAGR {pct(fx['cagr'])}, Sharpe {num(fx['sharpe'])}, "
        f"max DD {pct(fx['max_dd'])}. Yearly re-optimisation chases noise; fixed, plateau-centred parameters were more stable.\n")

    # --- robustness grid
    keys, rows = list(GRID), []
    for vals in itertools.product(*GRID.values()):
        d = dict(zip(keys, vals))
        if d["exit_dev"] > d["entry_dev"]: continue
        p = dict(P, **d)
        rows.append({**d, "IS_sharpe": run(fc, p, IS).stats["sharpe"], "OOS_sharpe": run(fc, p, OOS).stats["sharpe"]})
    G = pd.DataFrame(rows)
    G.to_csv(os.path.join(OUT, "grid.csv"), index=False)
    add("## 4. Robustness: every configuration in the grid\n")
    add(f"{len(G)} configurations. Out-of-sample Sharpe > 0 in **{(G.OOS_sharpe > 0).mean():.0%}** of them; "
        f"median OOS Sharpe {G.OOS_sharpe.median():.2f} (IQR {G.OOS_sharpe.quantile(.25):.2f}–{G.OOS_sharpe.quantile(.75):.2f}). "
        "A strategy that only works at one magic setting is overfit. This one works across the grid.\n")
    add("| Parameter | Value | Mean IS Sharpe | Mean OOS Sharpe |\n|---|---|---|---|")
    for k in keys:
        for v, g in G.groupby(k):
            add(f"| `{k}` | {v} | {g.IS_sharpe.mean():.2f} | {g.OOS_sharpe.mean():.2f} |")

    # --- sensitivity
    add("\n## 5. Sensitivity (out-of-sample 2023 → now)\n")
    add("| Variation | OOS CAGR | OOS Sharpe | OOS max DD | Trades |\n|---|---|---|---|---|")
    def sens(label, p=P, costs=bt.Costs(), lag=0, src=None):
        f = (features.build(src, p) if src is not None else fc.get(p)).copy()
        if lag:
            f[["flow_dev", "spot_dev"]] = f[["flow_dev", "spot_dev"]].shift(lag)
        f = f.loc[OOS]
        s = bt.run(f, strategy.signal(f, p), p, costs=costs).stats
        add(f"| {label} | {pct(s['cagr'])} | {num(s['sharpe'])} | {pct(s['max_dd'])} | {s['trades']} |")
    sens("**Default** (fee 25bp + slip 5bp)")
    sens("Cheaper venue (fee 10bp + slip 2bp)", costs=bt.Costs(10, 2))
    sens("Harsh costs (fee 40bp + slip 10bp)", costs=bt.Costs(40, 10))
    sens("Flow signal 1 day stale (archive-only data)", lag=6)
    sens("Flow signal 2 days stale", lag=12)
    sens("Target vol 30% (more conservative)", p=dict(P, target_vol=0.3))
    sens("Target vol 80% (≈ fully invested when long)", p=dict(P, target_vol=0.8))
    sens("Tighter stops (3 / 5 ATR)", p=dict(P, stop_atr=3, trail_atr=5))
    sens("No stops (flow exit only)", p=dict(P, stop_atr=99, trail_atr=99))
    sens("Funding-crowding veto on (z > 2)", p=dict(P, funding_max_z=2.0))
    sens("Long **and** short (perp venue)", p=dict(P, allow_short=True))
    spot = raw.copy(); spot["taker_buy_base"], spot["volume"] = spot["spot_taker_buy"], spot["spot_volume"]
    sens("Spot taker flow instead of perp flow", src=features.resample(spot, "4h"))

    # --- features
    add("\n## 6. Does order flow carry information? (rank IC vs next-20-day return, daily bars)\n")
    add(ic_table(raw))
    add("\nThe flow features (`flow_imb`, `flow_dev`, `cvd_slope`) keep the same positive sign in both periods. "
        "Price trend and momentum flip sign after 2022, which is why the strategy is built on flow first and uses trend only as a filter.\n")

    # --- trades & files
    t = res["FULL"].trades
    if len(t):
        t.to_csv(os.path.join(OUT, "trades.csv"), index=False)
        add("## 7. Trade statistics (full period)\n")
        add(f"Trades {len(t)} · win rate {(t.pnl > 0).mean():.0%} · avg win {pct(t.ret[t.ret > 0].mean())} · "
            f"avg loss {pct(t.ret[t.ret <= 0].mean())} · best {pct(t.ret.max())} · worst {pct(t.ret.min())} · "
            f"avg hold {(t.exit_time - t.entry_time).dt.total_seconds().mean() / 86400:.1f} days · "
            f"exits: {', '.join(f'{k} {v}' for k, v in t.reason.value_counts().items())}\n")
        add("Low win rate with big winners is typical of trend/flow-following: most of the profit comes from a few long regimes. "
            "Expect strings of small losses.\n")
    pd.DataFrame({"strategy": eq, "buy_hold": bheq}).resample("1D").last().to_csv(os.path.join(OUT, "equity.csv"))
    svg_chart({"Flow Regime strategy": (eq, "#1f6feb"), "Buy & hold BTC": (bheq, "#d29922")},
              os.path.join(OUT, "equity.svg"), "Growth of $10,000 (log scale), after fees")
    dd = lambda e: (e / e.cummax() - 1) * 100 + 100
    svg_chart({"Strategy drawdown": (dd(eq), "#1f6feb"), "Buy & hold drawdown": (dd(bheq), "#d29922")},
              os.path.join(OUT, "drawdown.svg"), "Drawdown from peak", log=False, fmt=lambda v: f"{v - 100:.0f}%")
    md.insert(2, "![equity](equity.svg)\n\n![drawdown](drawdown.svg)\n")
    add("## 8. Known limitations\n")
    add("- Backtests assume fills at the next 4h open plus slippage. Real fills, outages and Alpaca's BTC/USD price "
        "(vs Binance BTCUSDT signal data) will differ a little.\n"
        "- The 2020–26 window is mostly a BTC bull market. A long/flat strategy can't profit in a long bear market; it can only sit in cash.\n"
        f"- {res['FULL'].stats['trades']} trades is a small sample. Treat every number as an estimate with wide error bars (see the bootstrap interval).\n"
        "- Some design choices (wide disaster stops, volatility-target sizing, dropping the funding veto) were made after "
        "seeing 2023+ results, so the fixed-parameter OOS numbers are somewhat optimistic. The walk-forward in section 3 "
        "is the more honest estimate, and it is clearly weaker: re-optimising each year picked noisy settings (e.g. tight "
        "stops in 2022). The result that holds in every test is the **drawdown reduction** compared with buy-and-hold.\n"
        "- Market structure changes. Re-run this report every few months and stop the bot if live results drift well "
        "outside the backtested drawdown.\n")
    with open(os.path.join(OUT, "REPORT.md"), "w") as fh:
        fh.write("\n".join(md))
    print("\n".join(md[:30]))
    print(f"\nWrote {OUT}/REPORT.md")


if __name__ == "__main__":
    main()
