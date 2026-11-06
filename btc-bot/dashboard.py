#!/usr/bin/env python3
"""
Paper-trading dashboard: one self-contained HTML page (no cloud, no internet needed to view it).

  python dashboard.py            build dashboard.html from your bot files (equity_S*.csv, bot_log_S*.csv, state_S*.json)
  python dashboard.py --demo     build dashboard_demo.html from recent backtest data, to preview the layout
  python dashboard.py --open     build, then open it in your browser

run_bot.bat / run_bots.sh rebuild it after every hourly run, so just keep the page open (it reloads itself every 10 minutes).
"""
import argparse, csv, datetime as dt, html, json, os, sys, webbrowser
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from btc_bot import strategy

# Backtest expectations from the sign-off packs (reports/SIGNOFF_S1.md, SIGNOFF_S2.md)
EXPECT = {"S1": dict(win=0.42, avg_loss=-0.035), "S2": dict(win=0.65, avg_loss=-0.036)}
COLORS = {"S1": "var(--s1)", "S2": "var(--s2)", "BTC": "var(--btc)"}


# ---------------------------------------------------------------- data
def read_csv(path):
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []


def load_live(sid):
    st_p = os.path.join(HERE, f"state_{sid}.json")
    eq = read_csv(os.path.join(HERE, f"equity_{sid}.csv"))
    e = pd.DataFrame(eq)
    if len(e):
        e.index = pd.to_datetime(e["time"], utc=True)
        e = e.drop(columns="time")
        for c in ("equity", "btc_price", "btc_qty"):
            e[c] = e[c].astype(float)
    lim_p = os.path.join(HERE, "reports", f"survival_limits_{sid}.json")
    signed_p = os.path.join(HERE, f"SIGNED_OFF_{sid}.json")
    return dict(
        equity=e,
        log=read_csv(os.path.join(HERE, f"bot_log_{sid}.csv")),
        state=json.load(open(st_p)) if os.path.exists(st_p) else {},
        limits=json.load(open(lim_p)) if os.path.exists(lim_p) else {},
        signed=json.load(open(signed_p)) if os.path.exists(signed_p) else None,
    )


def load_demo(sid, days=240):
    """Recent backtest, shaped like live bot files, so the layout can be previewed."""
    from btc_bot import data, features, backtest as bt
    raw = data.load_dataset(start="2024-06-01")
    df = features.resample(raw, "4h")
    p = strategy.params(sid)
    f = features.build(df, p)
    ff = f.iloc[-days * 6:]
    res = bt.run(ff, strategy.signal(ff, p), p, costs=bt.Costs(25, 5))
    reg = strategy.regime(ff, p)
    e = pd.DataFrame({"equity": res.equity, "btc_price": ff.close, "regime": reg})
    e["btc_qty"] = 0.0
    log = []
    for _, t in res.trades.iterrows():
        log.append(dict(time=str(t.entry_time), action="ENTRY", price=f"{t.entry:.0f}", detail="demo"))
        if t.reason != "end":
            log.append(dict(time=str(t.exit_time), action="EXIT", price=f"{t.exit:.0f}", detail=t.reason))
    log.sort(key=lambda r: r["time"])
    st = dict(closed_trades=[dict(ret=r) for r in res.trades.ret[res.trades.reason != "end"]],
              peak_equity=float(res.equity.max()), halted=False, regime=str(reg.iloc[-1]),
              stop=None)
    lim = json.load(open(os.path.join(HERE, "reports", f"survival_limits_{sid}.json")))
    return dict(equity=e, log=log, state=st, limits=lim, signed=None)


# ---------------------------------------------------------------- metrics
def metrics(sid, d):
    e, st, lim = d["equity"], d["state"], d["limits"]
    rets = np.array([t["ret"] for t in st.get("closed_trades", [])])
    m = dict(sid=sid, name=strategy.VARIANTS[sid]["name"], version=strategy.VARIANTS[sid]["version"],
             fingerprint=strategy.fingerprint(strategy.params(sid)), trades=len(rets),
             signed=d["signed"], halted=bool(st.get("halted")), regime=st.get("regime") or "—", limits=lim)
    if len(e):
        eq = e["equity"]
        m.update(equity=eq.iloc[-1], start_equity=eq.iloc[0], ret=eq.iloc[-1] / eq.iloc[0] - 1,
                 btc_ret=e["btc_price"].iloc[-1] / e["btc_price"].iloc[0] - 1,
                 dd=eq.iloc[-1] / max(eq.max(), st.get("peak_equity") or 0) - 1, max_dd=(eq / eq.cummax() - 1).min(),
                 since=e.index[0], updated=e.index[-1], in_pos=bool(e["btc_qty"].iloc[-1] > 0))
    m["win"] = (rets > 0).mean() if len(rets) else None
    m["avg_loss"] = rets[rets <= 0].mean() if (rets <= 0).any() else None
    m["avg_win"] = rets[rets > 0].mean() if (rets > 0).any() else None
    streak = 0
    for r in rets[::-1]:
        if r > 0: break
        streak += 1
    m["streak"] = streak
    status, why = "OK", "Inside every survival limit."
    if lim and "dd" in m:
        if m["dd"] <= lim["dd_halt"] or streak >= lim["max_consecutive_losses"]:
            status, why = "HALT", "A halt limit is breached."
        elif m["dd"] <= lim["dd_warn"] or streak >= lim.get("warn_consecutive_losses", 99):
            status, why = "WARN", "Beyond backtest experience. Review, but this alone is not a halt."
    if m["halted"]:
        status, why = "HALT", "The bot has halted itself. Review, then run --reset-halt."
    m["status"], m["status_why"] = status, why
    return m


# ---------------------------------------------------------------- svg
def chart(series, hlines=(), height=220, fmt=lambda v: f"{v:,.0f}", pct_axis=False):
    """series: [(label, pd.Series, css colour)] -> inline responsive SVG."""
    W, H, L, R, T, B = 760, height, 56, 12, 10, 26
    s_all = pd.concat([s for _, s, _ in series if len(s)] + [pd.Series([v for v, *_ in hlines])])
    if s_all.empty:
        return '<p class="muted">No data yet.</p>'
    lo, hi = float(s_all.min()), float(s_all.max())
    if hi - lo < 1e-9: hi, lo = hi + 1, lo - 1
    pad = (hi - lo) * 0.06; lo, hi = lo - pad, hi + pad
    times = pd.concat([s for _, s, _ in series if len(s)]).index
    t0, t1 = times.min().value, times.max().value
    t1 = t1 if t1 > t0 else t0 + 1
    X = lambda t: L + (t.value - t0) / (t1 - t0) * (W - L - R)
    Y = lambda v: T + (hi - v) / (hi - lo) * (H - T - B)
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" class="chart">']
    for v in np.linspace(lo + pad, hi - pad, 4):
        y = Y(v)
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{L - 6}" y="{y + 4:.1f}" text-anchor="end" class="axis">{fmt(v)}</text>')
    span_days = (t1 - t0) / 86_400e9
    ticks = pd.date_range(pd.Timestamp(t0, tz="UTC"), pd.Timestamp(t1, tz="UTC"), periods=5)
    for t in ticks:
        out.append(f'<text x="{X(t):.1f}" y="{H - 8}" text-anchor="middle" class="axis">'
                   f'{t.strftime("%b %d" if span_days < 400 else "%b %Y")}</text>')
    for v, label, cls in hlines:
        y = Y(v)
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="{cls}"/>'
                   f'<text x="{W - R - 4}" y="{y - 4:.1f}" text-anchor="end" class="hl-label {cls}-t">{html.escape(label)}</text>')
    for label, s, col in series:
        if len(s) < 2:
            continue
        pts = " ".join(f"{X(t):.1f},{Y(v):.1f}" for t, v in s.items())
        out.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2" stroke-linejoin="round"/>')
    out.append("</svg>")
    legend = "".join(f'<span class="key"><i style="background:{c}"></i>{html.escape(l)}</span>' for l, _, c in series)
    return f'<div class="legend">{legend}</div>' + "".join(out)


# ---------------------------------------------------------------- html
def pct(x, plus=True):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else (f"{x:+.1%}" if plus else f"{x:.0%}")


def tile(label, value, sub="", cls=""):
    return f'<div class="tile {cls}"><div class="t-label">{label}</div><div class="t-value">{value}</div><div class="t-sub">{sub}</div></div>'


def section(m, d):
    lim, sid = m["limits"], m["sid"]
    ex = EXPECT[sid]
    signed = (f'Signed off by {html.escape(m["signed"]["name"])} on {m["signed"]["date"][:10]}' if m["signed"]
              else "Not signed off: the bot runs dry only")
    tiles = [
        tile("Status", m["status"], m["status_why"], f"st-{m['status'].lower()}"),
        tile("Equity", f"${m['equity']:,.2f}" if "equity" in m else "—",
             f"since {m['since']:%b %d, %Y}" if "since" in m else "no runs logged yet"),
        tile("Return vs BTC", pct(m.get("ret")), f"BTC buy &amp; hold {pct(m.get('btc_ret'))}"),
        tile("Drawdown from peak", pct(m.get("dd")),
             f"warn {lim.get('dd_warn', 0):.0%} · halt {lim.get('dd_halt', 0):.0%}" if lim else ""),
        tile("Closed trades", str(m["trades"]), f"win rate {pct(m['win'], False)} (backtest {ex['win']:.0%})"),
        tile("Losing streak", str(m["streak"]),
             f"warn {lim.get('warn_consecutive_losses', '—')} · halt {lim.get('max_consecutive_losses', '—')}" if lim else ""),
        tile("Average loss", pct(m["avg_loss"]), f"backtest {ex['avg_loss']:+.1%} · review past {lim.get('avg_loss_review', 0):+.1%}" if lim else ""),
        tile("Regime", m["regime"], ("In a position" if m.get("in_pos") else "Flat (cash)")),
    ]
    e = d["equity"]
    if len(e):
        base = e["equity"].iloc[0]
        eq_line = e["equity"] / base * 100
        btc_line = e["btc_price"] / e["btc_price"].iloc[0] * 100
        growth = chart([(f"{sid} paper equity", eq_line, COLORS[sid]), ("BTC buy & hold", btc_line, COLORS["BTC"])],
                       hlines=[(100, "start", "hl-base")], fmt=lambda v: f"{v:.0f}")
        ddl = (e["equity"] / e["equity"].cummax() - 1) * 100
        dd_chart = chart([(f"{sid} drawdown", ddl, COLORS[sid])],
                         hlines=[(lim["dd_warn"] * 100, f"warn {lim['dd_warn']:.0%}", "hl-warn"),
                                 (lim["dd_halt"] * 100, f"halt {lim['dd_halt']:.0%}", "hl-halt")] if lim else [],
                         height=170, fmt=lambda v: f"{v:.0f}%")
    else:
        growth = dd_chart = '<p class="muted">No equity snapshots yet. They appear after the first <code>--trade</code> run.</p>'
    rows = "".join(f"<tr><td>{html.escape(r['time'][:16].replace('T', ' '))}</td><td><span class='act act-{r['action'].lower()}'>{r['action']}</span></td>"
                   f"<td class='num'>{html.escape(str(r.get('price', '')))}</td><td>{html.escape(r.get('detail', ''))}</td></tr>"
                   for r in reversed(d["log"][-15:]))
    events = (f"<table><thead><tr><th>Time (UTC)</th><th>Action</th><th class='num'>Price</th><th>Detail</th></tr></thead>"
              f"<tbody>{rows}</tbody></table>") if rows else '<p class="muted">No bot activity logged yet.</p>'
    return f"""
<section class="strategy">
  <header class="s-head"><span class="dot" style="background:{COLORS[sid]}"></span>
    <h2>{sid} · {html.escape(m['name'])}</h2>
    <span class="muted mono">v{m['version']} · {m['fingerprint']} · {signed}</span></header>
  <div class="tiles">{''.join(tiles)}</div>
  <div class="charts">
    <div class="card"><h3>Growth of 100 (paper equity vs BTC)</h3>{growth}</div>
    <div class="card"><h3>Drawdown against the survival limits</h3>{dd_chart}</div>
  </div>
  <div class="card"><h3>Recent bot activity</h3>{events}</div>
</section>"""


def compare_table(ms):
    def row(label, f):
        return f"<tr><th>{label}</th>" + "".join(f"<td>{f(m)}</td>" for m in ms) + "</tr>"
    head = "<tr><th></th>" + "".join(f"<th><span class='dot' style='background:{COLORS[m['sid']]}'></span>{m['sid']}</th>" for m in ms) + "</tr>"
    body = "".join([
        row("Status", lambda m: f"<span class='badge st-{m['status'].lower()}'>{m['status']}</span>"),
        row("Return", lambda m: pct(m.get("ret"))),
        row("BTC over same period", lambda m: pct(m.get("btc_ret"))),
        row("Max drawdown so far", lambda m: pct(m.get("max_dd"))),
        row("Closed trades", lambda m: str(m["trades"])),
        row("Win rate (backtest)", lambda m: f"{pct(m['win'], False)} ({EXPECT[m['sid']]['win']:.0%})"),
        row("Avg win / avg loss", lambda m: f"{pct(m['avg_win'])} / {pct(m['avg_loss'])}"),
        row("Losing streak", lambda m: str(m["streak"])),
    ])
    return f"<table class='compare'><thead>{head}</thead><tbody>{body}</tbody></table>"


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1b1f24;--muted:#5f6b7a;--line:#e3e7ec;--grid:#eef1f4;
--s1:#1f6feb;--s2:#8250df;--btc:#bf8700;--ok:#1a7f37;--warn:#9a6700;--halt:#cf222e;--okbg:#dafbe1;--warnbg:#fff8c5;--haltbg:#ffebe9}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0d1117;--card:#161b22;--ink:#e6edf3;--muted:#8d96a0;
--line:#30363d;--grid:#21262d;--s1:#58a6ff;--s2:#bc8cff;--btc:#d29922;--ok:#3fb950;--warn:#d29922;--halt:#f85149;
--okbg:#12261a;--warnbg:#2b2111;--haltbg:#2d1214}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:20px 16px 48px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:0}
h3{font-size:13px;margin:0 0 8px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.03em}
.muted{color:var(--muted)}.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}
.banner{background:var(--warnbg);border:1px solid var(--warn);color:var(--ink);padding:10px 12px;border-radius:8px;margin:12px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin-top:12px;overflow-x:auto}
.strategy{margin-top:28px}.s-head{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px;margin-top:12px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.t-label{font-size:12px;color:var(--muted)}.t-value{font-size:22px;font-weight:650;margin:2px 0;font-variant-numeric:tabular-nums}
.t-sub{font-size:12px;color:var(--muted)}
.st-ok{border-color:var(--ok)}.st-ok .t-value{color:var(--ok)}.st-warn{border-color:var(--warn);background:var(--warnbg)}
.st-warn .t-value{color:var(--warn)}.st-halt{border-color:var(--halt);background:var(--haltbg)}.st-halt .t-value{color:var(--halt)}
.badge{padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;border:1px solid}
.badge.st-ok{color:var(--ok);background:var(--okbg)}.badge.st-warn{color:var(--warn)}.badge.st-halt{color:var(--halt)}
.charts{display:grid;grid-template-columns:1fr;gap:0 12px}@media(min-width:900px){.charts{grid-template-columns:1fr 1fr}}
.chart{width:100%;height:auto;display:block}.grid{stroke:var(--grid)}.axis{fill:var(--muted);font-size:11px}
.hl-base{stroke:var(--muted);stroke-dasharray:3 4}.hl-warn{stroke:var(--warn);stroke-dasharray:6 4}.hl-halt{stroke:var(--halt);stroke-dasharray:6 4}
.hl-label{font-size:11px}.hl-base-t{fill:var(--muted)}.hl-warn-t{fill:var(--warn)}.hl-halt-t{fill:var(--halt)}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:var(--muted);margin-bottom:4px}
.key i{display:inline-block;width:14px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
td:last-child{white-space:normal}.num{text-align:right;font-variant-numeric:tabular-nums}
.compare td,.compare th{text-align:center}.compare tbody th{text-align:left;color:var(--muted);font-weight:500}
.act{font-size:11px;font-weight:600;padding:1px 6px;border-radius:4px;border:1px solid var(--line)}
.act-entry{color:var(--ok)}.act-exit,.act-halt{color:var(--halt)}.act-take_profit{color:var(--s2)}.act-warn{color:var(--warn)}
footer{margin-top:32px;font-size:12px;color:var(--muted)}
"""


def build(demo=False):
    sids = sorted(strategy.VARIANTS)
    data = {sid: (load_demo(sid) if demo else load_live(sid)) for sid in sids}
    ms = [metrics(sid, data[sid]) for sid in sids]
    now = dt.datetime.now(dt.timezone.utc)
    banner = ('<div class="banner"><b>DEMO DATA.</b> This preview uses the last ~8 months of <i>backtest</i> results to show the layout. '
              'It is not your paper account. Run <code>python dashboard.py</code> after the bots have run to see live results.</div>' if demo else "")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="600">
<title>BTC Paper Trading</title><style>{CSS}</style></head><body><main>
<h1>BTC paper trading: S1 vs S2</h1>
<div class="muted">Alpaca paper accounts · built {now:%Y-%m-%d %H:%M} UTC · reloads every 10 minutes · educational, not financial advice</div>
{banner}
<div class="card"><h3>Side by side</h3>{compare_table(ms)}
<p class="muted" style="margin:10px 0 0">With about 6 trades a year per strategy, win rates mean little for months. Early on, what matters is
that both stay inside their survival limits and that <code>python compare.py --parity</code> shows the bots trading on the backtest's bars.</p></div>
{''.join(section(m, data[m['sid']]) for m in ms)}
<footer>Source files: equity_S*.csv, bot_log_S*.csv, state_S*.json, reports/survival_limits_S*.json. Strategy rules and evidence:
reports/SIGNOFF_S1.md, reports/SIGNOFF_S2.md, STRATEGY_LOG.md.</footer>
</main></body></html>"""
    out = os.path.join(HERE, "dashboard_demo.html" if demo else "dashboard.html")
    open(out, "w", encoding="utf-8").write(page)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()
    out = build(a.demo)
    print(f"Wrote {out}")
    if a.open:
        webbrowser.open("file://" + out)


if __name__ == "__main__":
    main()
