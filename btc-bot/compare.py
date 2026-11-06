#!/usr/bin/env python3
"""
Side-by-side paper-trading scoreboard for S1 and S2.

  python compare.py            results from state/log files (no network)
  python compare.py --live     also read current equity from each Alpaca paper account (.env.S1 / .env.S2)
  python compare.py --parity   re-run the backtest over the paper-trading period and check the bot
                               traded on the same bars (the main thing paper trading has to prove)
"""
import argparse, csv, json, os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from btc_bot import strategy

SIDS = sorted(strategy.VARIANTS)


def read_env(path):
    out = {}
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load(sid):
    st_path, log_path = os.path.join(HERE, f"state_{sid}.json"), os.path.join(HERE, f"bot_log_{sid}.csv")
    st = json.load(open(st_path)) if os.path.exists(st_path) else {}
    log = list(csv.DictReader(open(log_path))) if os.path.exists(log_path) else []
    lim_path = os.path.join(HERE, "reports", f"survival_limits_{sid}.json")
    lim = json.load(open(lim_path)) if os.path.exists(lim_path) else {}
    return st, log, lim


def scoreboard(live):
    rows = {}
    for sid in SIDS:
        st, log, lim = load(sid)
        r = np.array([t["ret"] for t in st.get("closed_trades", [])])
        row = {
            "Strategy": f"{sid} v{strategy.VARIANTS[sid]['version']}",
            "Signed off": "yes" if os.path.exists(os.path.join(HERE, f"SIGNED_OFF_{sid}.json")) else "no",
            "First log entry": log[0]["time"][:10] if log else "—",
            "Closed trades": len(r),
            "Win rate": f"{(r > 0).mean():.0%}" if len(r) else "—",
            "Avg win": f"{r[r > 0].mean():+.1%}" if (r > 0).any() else "—",
            "Avg loss": f"{r[r <= 0].mean():+.1%}" if (r <= 0).any() else "—",
            "Worst trade": f"{r.min():+.1%}" if len(r) else "—",
            "Compounded return": f"{np.prod(1 + r) - 1:+.1%}" if len(r) else "—",
            "Halted": "YES" if st.get("halted") else "no",
            "Halt limits (DD / streak / single loss)": (f"{lim.get('dd_halt', 0):.0%} / {lim.get('max_consecutive_losses', '—')} / "
                                                       f"{lim.get('worst_single_loss', 0):.1%}") if lim else "—",
        }
        if live:
            env = read_env(os.path.join(HERE, f".env.{sid}"))
            if env.get("ALPACA_API_KEY_ID"):
                from btc_bot.alpaca import PaperClient
                c = PaperClient(env["ALPACA_API_KEY_ID"], env["ALPACA_API_SECRET_KEY"])
                eq = float(c.account()["equity"]); pk = max(st.get("peak_equity") or eq, eq)
                row["Equity now"] = f"${eq:,.2f}"; row["Drawdown from peak"] = f"{eq / pk - 1:+.1%}"
            else:
                row["Equity now"] = f"(no .env.{sid})"
        rows[sid] = row
    df = pd.DataFrame(rows)
    print(df.to_string())
    print("\nBacktest expectations: S1 win ~42%, avg loss ~-3.5%; S2 win ~65%, avg loss ~-3.6% (reports/SIGNOFF_S1.md, SIGNOFF_S2.md).")
    print("With ~6 trades a year, give it months before reading anything into the win rates. Halts are what matter early on.")


def parity():
    from btc_bot import data, features, backtest as bt
    raw = data.load_dataset(start="2024-06-01")
    df = features.resample(raw, "4h")
    for sid in SIDS:
        _, log, _ = load(sid)
        acts = [r for r in log if r["action"] in ("ENTRY", "EXIT", "TAKE_PROFIT")]
        if not log:
            print(f"{sid}: no log yet."); continue
        start = pd.Timestamp(log[0]["time"]).tz_convert("UTC")
        p = strategy.params(sid)
        f = features.build(df, p)
        ff = f.loc[start - pd.Timedelta(days=1):]
        res = bt.run(ff, strategy.signal(ff, p), p)
        exp = set()
        for _, t in res.trades.iterrows():
            exp.add(("ENTRY", str(t.entry_time - pd.Timedelta("4h"))))
            if t.reason != "end":
                exp.add(("EXIT", str(t.exit_time - pd.Timedelta("4h"))))
        got = {(r["action"], r["bar"]) for r in acts if r["action"] in ("ENTRY", "EXIT")}
        print(f"\n{sid}: backtest expected {len([e for e in exp if e[0] == 'ENTRY'])} entries since {start:%Y-%m-%d}; "
              f"bot logged {len([g for g in got if g[0] == 'ENTRY'])}.")
        for a in sorted(exp - got, key=lambda x: x[1]): print(f"  MISSING in bot: {a[0]} on bar {a[1]}")
        for a in sorted(got - exp, key=lambda x: x[1]): print(f"  EXTRA in bot (not in backtest): {a[0]} on bar {a[1]}")
        if exp == got: print("  Match: the bot traded on exactly the bars the backtest did.")
    print("\nStop exits can land one bar apart (the live stop rests on Alpaca and fills intrabar), so a 4h offset on an EXIT is normal.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--parity", action="store_true")
    a = ap.parse_args()
    scoreboard(a.live)
    if a.parity:
        parity()


if __name__ == "__main__":
    main()
