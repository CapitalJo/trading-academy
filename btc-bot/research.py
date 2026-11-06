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
  * Two venue profiles:
      spot_long_flat   Alpaca BTC/USD: long in BULL, cash in BEAR/RANGE. 0.25% fee + 0.05% slippage per side.
      perp_long_short  perpetual futures: long in BULL, short in BEAR, cash in RANGE.
                       0.05% fee + 0.03% slippage per side, plus actual historical funding paid/received.
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
            spot_confirm=[False, True], chop_adx_min=[None, 20])
WF_GRID = dict(flow_n=[42, 84, 126], entry_dev=[0.0, 0.005, 0.01], exit_dev=[-0.01, -0.005],
               chop_adx_min=[None, 20], stop=[(3.0, 5.0), (5.0, 8.0)])


def pct(x): return f"{x:+.1%}" if np.isfinite(x) else "n/a"
def num(x): return f"{x:.2f}" if np.isfinite(x) else "n/a"


class FeatCache:
    def __init__(self, df): self.df, self.c = df, {}
    def get(self, p):
        k = (p["flow_n"], p["base_n"], p["z_n"], p["ema_slow"])
        if k not in self.c: self.c[k] = features.build(self.df, p)
        return self.c[k]


COSTS = {"spot_long_flat": bt.Costs(25, 5), "perp_long_short": bt.Costs(5, 3, funding=True)}
LABEL = {"spot_long_flat": "Spot long/flat (Alpaca)", "perp_long_short": "Perp long/short"}


def prof(name, **kw):
    return {**P, **strategy.PROFILES[name], **kw}


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


def walk_forward(fc, years, profile, extra=None):
    keys = list(WF_GRID)
    chunks, picks = [], []
    for y in years:
        best, best_s = None, -np.inf
        for vals in itertools.product(*WF_GRID.values()):
            d = dict(zip(keys, vals))
            if d["exit_dev"] > d["entry_dev"]: continue
            sa, ta = d.pop("stop")
            p = prof(profile, **d, stop_atr=sa, trail_atr=ta, **(extra or {}))
            s = run(fc, p, slice("2020-06-01", f"{y - 1}-12-31"), COSTS[profile]).stats["sharpe"]
            if s > best_s: best, best_s = p, s
        r = run(fc, best, slice(f"{y}-01-01", f"{y}-12-31"), COSTS[profile])
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
    names = list(strategy.PROFILES)
    add("# BTC Flow Regime strategy: backtest report\n")
    add(f"_Generated by `research.py` on {pd.Timestamp.now('UTC'):%Y-%m-%d %H:%M} UTC. "
        f"Data: Binance BTCUSDT perpetual + spot, {df.index[0]:%Y-%m-%d} → {df.index[-1]:%Y-%m-%d}, "
        f"{len(df):,} × 4h bars. Educational research, not financial advice._\n")
    add("**Regimes.** BULL = price above its 200-bar EMA and ADX ≥ 20 → go long when buy-side flow confirms. "
        "BEAR = price below a *falling* 200-bar EMA and ADX ≥ 20 → go short when sell-side flow confirms "
        "(or hold cash on a venue that can't short). RANGE = ADX < 20 or mixed signals → **pause**: no new trades.\n")

    # --- headline
    periods = [("IS", IS, "In-sample 2020-06 → 2022-12"), ("OOS", OOS, "**Out-of-sample 2023-01 → now**"),
               ("FULL", FULL, "Full period")]
    res = {(n, k): run(fc, prof(n), sl, COSTS[n]) for n in names for k, sl, _ in periods}
    bh = {k: bt.buy_and_hold(fc.get(P).loc[sl]) for k, sl, _ in periods}
    add("## 1. Headline results (after costs, no leverage)\n")
    add("Costs: spot = 0.25% fee + 0.05% slippage per side (Alpaca base tier). Perp = 0.05% + 0.03% per side "
        "plus the real historical funding paid or received every 8h.\n")
    add(HDR)
    for k, _, lab in periods:
        for n in names:
            add(row(f"{LABEL[n]}: {lab}", res[(n, k)].stats))
        add(row(f"Buy & hold: {lab}", bh[k].stats))
    for n in names:
        ci, p_pos = bootstrap_sharpe(res[(n, "OOS")].equity)
        add(f"\n{LABEL[n]}, OOS Sharpe 90% block-bootstrap interval: **{ci[0]:.2f} – {ci[2]:.2f}** "
            f"(median {ci[1]:.2f}; {p_pos:.0%} of resamples > 0).")
    add("\n**How to read this:** 2020–26 was mostly a BTC bull market, so buy-and-hold made the most money. The strategy "
        "gives up part of that upside to avoid most of the crashes. The long/short profile also earns in bear markets "
        "(see 2022 below), but it pays a small premium in bull markets when shorts get stopped out.\n")

    # --- yearly
    add("## 2. Calendar years, by profile\n")
    add("| Year | Spot long/flat | Perp long/short | Buy & hold | Long/flat max DD | Long/short max DD | B&H max DD |\n|---|---|---|---|---|---|---|")
    eqs = {n: res[(n, "FULL")].equity for n in names}
    bheq = bh["FULL"].equity
    for y in sorted(set(bheq.index.year)):
        e = {n: eqs[n][eqs[n].index.year == y] for n in names}
        b = bheq[bheq.index.year == y]
        ret = lambda x: pct(x.iloc[-1] / x.iloc[0] - 1)
        mdd = lambda x: pct((x / x.cummax() - 1).min())
        add(f"| {y} | {ret(e[names[0]])} | {ret(e[names[1]])} | {ret(b)} | {mdd(e[names[0]])} | {mdd(e[names[1]])} | {mdd(b)} |")

    # --- regimes
    pp = prof("perp_long_short")
    f_full = fc.get(pp).loc[FULL]
    reg = strategy.regime(f_full, pp)
    bar_ret = f_full.close.pct_change().shift(-1)
    add("\n## 3. Regime breakdown (full period)\n")
    add("| Regime | Share of time | BTC avg 4h return in regime | Long/short trades entered | Their total return | Win rate |\n|---|---|---|---|---|---|")
    t = res[("perp_long_short", "FULL")].trades.copy()
    t["regime"] = reg.reindex(t.entry_time - pd.Timedelta(P["timeframe"])).to_numpy() if len(t) else []
    for r in ["BULL", "BEAR", "RANGE"]:
        m = reg == r
        tr_r = t[t.regime == r] if len(t) else t
        add(f"| {r} | {m.mean():.0%} | {bar_ret[m].mean():+.3%} | {len(tr_r)} | "
            f"{pct(tr_r.ret.sum()) if len(tr_r) else 'n/a'} | {(tr_r.pnl > 0).mean():.0%} |" if len(tr_r) else
            f"| {r} | {m.mean():.0%} | {bar_ret[m].mean():+.3%} | 0 (paused) | n/a | n/a |")
    if len(t):
        for side in ["long", "short"]:
            ts = t[t.side == side]
            add(f"\n{side.title()} trades: {len(ts)}, total return {pct(ts.ret.sum())}, win rate {(ts.pnl > 0).mean():.0%}, "
                f"IS {pct(ts[ts.entry_time < '2023-01-01'].ret.sum())} / OOS {pct(ts[ts.entry_time >= '2023-01-01'].ret.sum())}.")
    rc = reg.resample("1D").last()
    add("\nRegime timeline (one character per week: **+** bull, **−** bear, **·** range):\n")
    wk = reg.resample("W").agg(lambda x: x.value_counts().idxmax() if len(x) else "RANGE")
    for y in sorted(set(wk.index.year)):
        w = wk[wk.index.year == y]
        add(f"`{y}  " + "".join({"BULL": "+", "BEAR": "-", "RANGE": "·"}[v] for v in w) + "`  ")

    # --- ablation
    add("\n## 4. What each rule adds (ablation)\n")
    add("| Variant | IS Sharpe | OOS Sharpe | 2022 return | OOS max DD |\n|---|---|---|---|---|")
    def abl(label, n, costs=None, **kw):
        p, costs = prof(n, **kw), costs or COSTS[n]
        a, b = run(fc, p, IS, costs).stats, run(fc, p, OOS, costs).stats
        c = run(fc, p, slice("2022-01-01", "2022-12-31"), costs).stats
        add(f"| {label} | {num(a['sharpe'])} | {num(b['sharpe'])} | {pct(c['total_return'])} | {pct(b['max_dd'])} |")
    abl("**Spot long/flat (default)**", "spot_long_flat")
    abl("Spot long/flat, no consolidation pause", "spot_long_flat", chop_adx_min=None)
    abl("**Perp long/short (default)**", "perp_long_short")
    abl("Perp long/short, no consolidation pause", "perp_long_short", chop_adx_min=None)
    abl("Perp long/short, shorts without falling-EMA rule", "perp_long_short", short_slope=False)
    abl("Perp long/short, also EXIT on consolidation", "perp_long_short", chop_exit=True)
    abl("Perp long/short, pause at ADX < 25", "perp_long_short", chop_adx_min=25)
    abl("Perp long only (no shorts), perp costs", "perp_long_short", allow_short=False)
    abl("Perp long/short at Alpaca-level costs (0.30%/side), no funding", "perp_long_short", costs=bt.Costs(25, 5))

    # --- walk forward
    years = list(range(2022, pd.Timestamp.now('UTC').year + 1))
    add("\n## 5. Walk-forward (parameters re-chosen every January using only past data)\n")
    add(f"Grid per refit: {int(np.prod([len(v) for v in WF_GRID.values()]))} combos (flow window, entry/exit thresholds, "
        "consolidation pause on/off, stop width). Objective: in-sample Sharpe. Then trade the next year blind.\n")
    bh_wf = bt.buy_and_hold(fc.get(P).loc[f"{years[0]}-01-01":])
    for n in names:
        wf_eq, picks = walk_forward(fc, years, n)
        wf_s = bt.stats(wf_eq, pd.DataFrame())
        fx = run(fc, prof(n), slice(f"{years[0]}-01-01", None), COSTS[n]).stats
        add(f"**{LABEL[n]}**\n")
        add("| Year | flow_n | entry/exit dev | pause ADX | stop/trail | Train Sharpe | Year return | Year Sharpe | Year max DD |\n|---|---|---|---|---|---|---|---|---|")
        for y, p, s_tr, st in picks:
            add(f"| {y} | {p['flow_n']} | {p['entry_dev']}/{p['exit_dev']} | {p['chop_adx_min']} | {p['stop_atr']}/{p['trail_atr']} | "
                f"{s_tr:.2f} | {pct(st['total_return'])} | {num(st['sharpe'])} | {pct(st['max_dd'])} |")
        add(f"\nStitched {years[0]}→now: CAGR **{pct(wf_s['cagr'])}**, Sharpe **{num(wf_s['sharpe'])}**, max DD **{pct(wf_s['max_dd'])}**. "
            f"Fixed defaults, same span: CAGR {pct(fx['cagr'])}, Sharpe {num(fx['sharpe'])}, max DD {pct(fx['max_dd'])}.\n")
    add(f"Buy & hold, same span: CAGR {pct(bh_wf.stats['cagr'])}, Sharpe {num(bh_wf.stats['sharpe'])}, max DD {pct(bh_wf.stats['max_dd'])}.\n")

    # --- robustness grid
    add("## 6. Robustness: every configuration in the grid\n")
    keys = list(GRID)
    for n in names:
        rows = []
        for vals in itertools.product(*GRID.values()):
            d = dict(zip(keys, vals))
            if d["exit_dev"] > d["entry_dev"]: continue
            p = prof(n, **d)
            rows.append({**d, "IS_sharpe": run(fc, p, IS, COSTS[n]).stats["sharpe"],
                         "OOS_sharpe": run(fc, p, OOS, COSTS[n]).stats["sharpe"]})
        G = pd.DataFrame(rows)
        G.to_csv(os.path.join(OUT, f"grid_{n}.csv"), index=False)
        add(f"- **{LABEL[n]}**: {len(G)} configs. OOS Sharpe > 0 in **{(G.OOS_sharpe > 0).mean():.0%}**, "
            f"median OOS Sharpe {G.OOS_sharpe.median():.2f} (IQR {G.OOS_sharpe.quantile(.25):.2f}–{G.OOS_sharpe.quantile(.75):.2f}); "
            f"median IS Sharpe {G.IS_sharpe.median():.2f}.")
    add("")

    # --- sensitivity (spot profile, the one the Alpaca bot runs)
    add("## 7. Sensitivity, spot long/flat on Alpaca (out-of-sample 2023 → now)\n")
    add("| Variation | OOS CAGR | OOS Sharpe | OOS max DD | Trades |\n|---|---|---|---|---|")
    PS = prof("spot_long_flat")
    def sens(label, p=PS, costs=bt.Costs(), lag=0, src=None):
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
    sens("Target vol 30% (more conservative)", p=dict(PS, target_vol=0.3))
    sens("Target vol 80% (≈ fully invested when long)", p=dict(PS, target_vol=0.8))
    sens("Tighter stops (3 / 5 ATR)", p=dict(PS, stop_atr=3, trail_atr=5))
    sens("No stops (flow exit only)", p=dict(PS, stop_atr=99, trail_atr=99))
    sens("Funding-crowding veto on (z > 2)", p=dict(PS, funding_max_z=2.0))
    spot = raw.copy(); spot["taker_buy_base"], spot["volume"] = spot["spot_taker_buy"], spot["spot_volume"]
    sens("Spot taker flow instead of perp flow", src=features.resample(spot, "4h"))

    # --- features
    add("\n## 8. Does order flow carry information? (rank IC vs next-20-day return, daily bars)\n")
    add(ic_table(raw))
    add("\nThe flow features (`flow_imb`, `flow_dev`, `cvd_slope`) keep the same positive sign in both periods. "
        "Price trend and momentum flip sign after 2022, which is why flow drives entries and exits, and trend/ADX only "
        "decide *which side* is allowed and *whether to trade at all*.\n")

    # --- trades & files
    add("## 9. Trade statistics (full period)\n")
    for n in names:
        t = res[(n, "FULL")].trades
        if len(t):
            t.to_csv(os.path.join(OUT, f"trades_{n}.csv"), index=False)
            add(f"- **{LABEL[n]}**: {len(t)} trades · win rate {(t.pnl > 0).mean():.0%} · avg win {pct(t.ret[t.ret > 0].mean())} · "
                f"avg loss {pct(t.ret[t.ret <= 0].mean())} · worst {pct(t.ret.min())} · "
                f"avg hold {(t.exit_time - t.entry_time).dt.total_seconds().mean() / 86400:.1f} days · "
                f"exits: {', '.join(f'{k} {v}' for k, v in t.reason.value_counts().items())}")
    add("\nA low win rate with large winners is normal for flow/trend following. Expect strings of small losses.\n")
    pd.DataFrame({**{n: eqs[n] for n in names}, "buy_hold": bheq}).resample("1D").last().to_csv(os.path.join(OUT, "equity.csv"))
    svg_chart({"Spot long/flat": (eqs[names[0]], "#1f6feb"), "Perp long/short": (eqs[names[1]], "#8250df"),
               "Buy & hold BTC": (bheq, "#d29922")}, os.path.join(OUT, "equity.svg"), "Growth of $10,000 (log scale), after costs")
    dd = lambda e: (e / e.cummax() - 1) * 100 + 100
    svg_chart({"Spot long/flat": (dd(eqs[names[0]]), "#1f6feb"), "Perp long/short": (dd(eqs[names[1]]), "#8250df"),
               "Buy & hold": (dd(bheq), "#d29922")}, os.path.join(OUT, "drawdown.svg"), "Drawdown from peak",
              log=False, fmt=lambda v: f"{v - 100:.0f}%")
    md.insert(3, "![equity](equity.svg)\n\n![drawdown](drawdown.svg)\n")
    add("## 10. Known limitations\n")
    add("- Fills are assumed at the next 4h open plus slippage. Real fills, outages, and the gap between the execution venue's "
        "price and the Binance signal data will differ a little.\n"
        "- Perp costs assume a liquid perp venue. US-accessible venues (CME micro futures, Coinbase/Kraken US perps) have different "
        "fees, contract sizes, trading hours (CME closes at weekends) and basis/roll costs instead of funding.\n"
        f"- {res[('spot_long_flat', 'FULL')].stats['trades']}–{res[('perp_long_short', 'FULL')].stats['trades']} trades is a small "
        "sample. Treat every number as an estimate with wide error bars (see the bootstrap intervals).\n"
        "- Several design choices (wide disaster stops, vol sizing, ADX 20 pause, falling-EMA rule for shorts) were made with "
        "2023+ results visible, so the fixed-parameter OOS numbers are optimistic. The walk-forward in section 5 is the more honest "
        "estimate. The result that holds in every test is the **drawdown reduction** compared with buy-and-hold.\n"
        "- The consolidation pause has only a small measured effect. ADX-style chop filters carry weak, unstable information "
        "about BTC's next move.\n")
    with open(os.path.join(OUT, "REPORT.md"), "w") as fh:
        fh.write("\n".join(md))
    print("\n".join(md))
    print(f"\nWrote {OUT}/REPORT.md")


if __name__ == "__main__":
    main()
