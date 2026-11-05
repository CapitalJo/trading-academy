#!/usr/bin/env python3
"""
BTC Flow Regime bot: trades BTC/USD on an Alpaca PAPER account.

  python bot.py                 dry run: fetch data, compute the signal, print the plan (no orders)
  python bot.py --trade         same, then send the orders to the Alpaca PAPER account
  python bot.py --status        show account, position, bot state
  python bot.py --reset-halt    clear the drawdown kill switch after you've reviewed what happened

Run it every hour (Task Scheduler / cron / GitHub Actions). Decisions are only made on CLOSED 4h bars,
so extra runs are harmless: the bot is idempotent per bar.

Regimes (see btc_bot/strategy.py)
  BULL  -> long when 14-day buy-side flow confirms
  BEAR  -> cash on Alpaca (spot can't be shorted); the short signal is still shown so you can see it
  RANGE -> pause: no new entries while ADX < 20. Open trades keep their flow exit and stops.

Risk controls
  * Position size from volatility targeting (strategy.DEFAULTS["target_vol"]), never > 100% of equity
    (no leverage) and never more than --max-notional dollars.
  * Disaster stop: a resting GTC stop-limit sell on Alpaca at entry - 5 ATR, ratcheted up as a
    chandelier trail (best close - 8 ATR). The bot also checks the stop itself each run.
  * Kill switch: if equity falls more than --halt-dd (default 35%) from its peak, the bot flattens and
    stops trading until you run --reset-halt. The backtest's worst drawdown was ~31%. Beyond that,
    the strategy is behaving outside what was tested.
  * Stale-data guard: no new entries if the newest bar is more than 6 hours old.
"""
import argparse, csv, datetime as dt, json, os, sys, time
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from btc_bot import features, strategy, live_data, backtest as bt
from btc_bot.alpaca import PaperClient, load_env

STATE = os.path.join(HERE, "state.json")
LOG = os.path.join(HERE, "bot_log.csv")
# Alpaca crypto is spot-only: BULL -> long, BEAR -> cash (short signals are reported, not traded), RANGE -> pause.
VENUE = "spot_long_flat"
P = {**strategy.DEFAULTS, **strategy.PROFILES[VENUE]}


def load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {"last_bar": None, "stop": None, "best_close": None, "entry_px": None, "entry_time": None,
            "stop_order_id": None, "peak_equity": None, "halted": False, "regime": None}


def save_state(s):
    tmp = STATE + ".tmp"
    json.dump(s, open(tmp, "w"), indent=2)
    os.replace(tmp, STATE)


def log(row):
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["time", "bar", "action", "price", "detail"])
        if new: w.writeheader()
        w.writerow(row)


def compute_signal():
    raw, info = live_data.load_live()
    df = features.resample(raw, P["timeframe"])
    now = pd.Timestamp.now(tz="UTC")
    df = df[df.index + pd.Timedelta(P["timeframe"]) <= now]            # closed bars only
    f = features.build(df, P)
    sig = strategy.signal(f, P)
    sig_ls = strategy.signal(f, {**P, "allow_short": True})
    reg = strategy.regime(f, P)
    i = len(f) - 1
    last = f.iloc[i]
    bpy = pd.Timedelta(days=365) / pd.Timedelta(P["timeframe"])
    frac = bt.position_fraction(P, last.atr, last.close, last.rvol, 0.02, 1.0, bpy)
    return dict(bar=str(f.index[i]), close=float(last.close), atr=float(last.atr),
                flow_dev=float(last.flow_dev), spot_dev=float(last.spot_dev), ema_slow=float(last.ema_slow),
                entry=bool(sig["entry_long"][i]), exit=bool(sig["exit_long"][i]), frac=frac,
                regime=str(reg.iloc[i]), adx=float(last.adx), short_signal=bool(sig_ls["entry_short"][i]),
                age_h=(now - (f.index[i] + pd.Timedelta(P["timeframe"]))).total_seconds() / 3600, info=info)


REGIME_TEXT = {
    "BULL": "uptrend: long trades allowed when buy-side flow confirms",
    "BEAR": "downtrend: Alpaca can't short, so the bot holds cash",
    "RANGE": "consolidation (ADX < {adx_min}): PAUSED, no new trades",
}


def describe(s):
    print(f"Regime: {s['regime']} | ADX {s['adx']:.1f} | " + REGIME_TEXT[s["regime"]].format(adx_min=P["chop_adx_min"]))
    if s["short_signal"]:
        print("  Bear flow signal active: a short venue would be opening a short now.")
    print(f"Last closed 4h bar: {s['bar']}  close={s['close']:,.0f}  ATR={s['atr']:,.0f}")
    print(f"  flow_dev={s['flow_dev']:+.4f} (enter > {P['entry_dev']}, exit < {P['exit_dev']})  "
          f"spot_dev={s['spot_dev']:+.4f}  above EMA{P['ema_slow']}={s['close'] > s['ema_slow']}")
    print(f"  ENTRY signal={s['entry']}  EXIT signal={s['exit']}  vol-target size={s['frac']:.0%} of equity")
    print(f"  data: {s['info']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trade", action="store_true", help="send orders to the Alpaca PAPER account")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--reset-halt", action="store_true")
    ap.add_argument("--max-notional", type=float, default=25_000, help="cap on $ per position (default 25000)")
    ap.add_argument("--halt-dd", type=float, default=0.35, help="kill-switch drawdown from peak (default 0.35)")
    a = ap.parse_args()
    load_env(os.path.join(HERE, ".env"))
    st = load_state()

    if a.reset_halt:
        st["halted"] = False; st["peak_equity"] = None; save_state(st); print("Halt cleared."); return

    s = compute_signal()
    describe(s)
    if not (a.trade or a.status):
        print("\nDry run. Add --trade to send orders to your Alpaca PAPER account.")
        return

    c = PaperClient.from_env()
    acct, pos = c.account(), c.position()
    equity = float(acct["equity"])
    qty = float(pos["qty"]) if pos else 0.0
    print(f"\nPaper equity ${equity:,.2f}  BTC qty {qty:.6f}  state={json.dumps({k: st[k] for k in ('stop', 'best_close', 'halted')})}")
    if a.status:
        return
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if st.get("regime") != s["regime"]:
        log(dict(time=now, bar=s["bar"], action="REGIME", price=s["close"],
                 detail=f"{st.get('regime')} -> {s['regime']} (ADX {s['adx']:.1f})"))
        st["regime"] = s["regime"]
    st["peak_equity"] = max(st.get("peak_equity") or equity, equity)

    def flatten(why):
        for o in c.open_orders():
            c.cancel(o["id"])
        time.sleep(1)
        if c.position():
            c.close_position()
        log(dict(time=now, bar=s["bar"], action="EXIT", price=s["close"], detail=why))
        print(f">>> EXIT ({why})")
        st.update(stop=None, best_close=None, entry_px=None, entry_time=None, stop_order_id=None)

    # kill switch
    if not st["halted"] and equity < st["peak_equity"] * (1 - a.halt_dd):
        st["halted"] = True
        if qty > 0: flatten(f"kill switch: equity {equity:,.0f} < {1 - a.halt_dd:.0%} of peak {st['peak_equity']:,.0f}")
        log(dict(time=now, bar=s["bar"], action="HALT", price=s["close"], detail="drawdown kill switch"))
    if st["halted"]:
        print("HALTED by drawdown kill switch. Review, then run: python bot.py --reset-halt"); save_state(st); return

    in_pos = qty * s["close"] > 10      # ignore dust
    if in_pos:
        # adopt a position we don't have state for (e.g. after a manual buy)
        if st["stop"] is None:
            st.update(entry_px=float(pos["avg_entry_price"]), best_close=s["close"],
                      stop=float(pos["avg_entry_price"]) - P["stop_atr"] * s["atr"])
        new_bar = st["last_bar"] != s["bar"]
        if new_bar:
            st["best_close"] = max(st["best_close"], s["close"])
            st["stop"] = max(st["stop"], st["best_close"] - P["trail_atr"] * s["atr"])
        if s["exit"] and new_bar:
            flatten(f"flow exit (flow_dev {s['flow_dev']:+.4f})")
        elif s["close"] <= st["stop"]:
            flatten(f"stop {st['stop']:,.0f} breached")
        else:
            # keep exactly one resting stop-limit at the current stop level
            orders = c.open_orders()
            have = [o for o in orders if o["type"] == "stop_limit" and o["side"] == "sell"]
            ok = any(abs(float(o["stop_price"]) - st["stop"]) / st["stop"] < 0.005 for o in have)
            if not ok:
                for o in orders: c.cancel(o["id"])
                time.sleep(1)
                o = c.stop_limit_sell(qty, st["stop"], st["stop"] * 0.98, f"flow-stop-{int(time.time())}")
                st["stop_order_id"] = o["id"]
                log(dict(time=now, bar=s["bar"], action="STOP", price=st["stop"], detail=f"qty {qty:.6f}"))
                print(f">>> stop-limit set at {st['stop']:,.0f}")
            print(f"Holding. Stop {st['stop']:,.0f}, best close {st['best_close']:,.0f}")
    else:
        if st["stop"] is not None:      # stop order filled since last run
            log(dict(time=now, bar=s["bar"], action="EXIT", price=s["close"], detail="stop order filled on Alpaca"))
            st.update(stop=None, best_close=None, entry_px=None, entry_time=None, stop_order_id=None)
        if s["entry"] and st["last_bar"] != s["bar"]:
            if s["age_h"] > 6:
                print(f"Entry signal but data is {s['age_h']:.1f}h old. Skipping (stale-data guard).")
            else:
                usd = min(s["frac"] * equity, a.max_notional, float(acct.get("non_marginable_buying_power") or acct["cash"]) * 0.99)
                if usd >= 10:
                    o = c.market_buy_notional(usd, f"flow-entry-{s['bar'][:13]}")
                    for _ in range(20):
                        time.sleep(1.5)
                        o = c.order(o["id"])
                        if o["status"] == "filled": break
                    fill = float(o.get("filled_avg_price") or s["close"])
                    p = c.position()
                    q = float(p["qty"]) if p else 0.0
                    st.update(entry_px=fill, best_close=fill, entry_time=now, stop=fill - P["stop_atr"] * s["atr"])
                    if q > 0:
                        so = c.stop_limit_sell(q, st["stop"], st["stop"] * 0.98, f"flow-stop-{int(time.time())}")
                        st["stop_order_id"] = so["id"]
                    log(dict(time=now, bar=s["bar"], action="ENTRY", price=fill,
                             detail=f"${usd:,.0f} ({s['frac']:.0%} target), qty {q:.6f}, stop {st['stop']:,.0f}"))
                    print(f">>> ENTRY ${usd:,.0f} at ~{fill:,.0f}, stop {st['stop']:,.0f}")
        else:
            print({"BULL": "Flat. Bull regime but flow hasn't confirmed yet.",
                   "BEAR": "Flat. Bear regime: holding cash.",
                   "RANGE": "Flat. Consolidation: paused."}[s["regime"]])
    st["last_bar"] = s["bar"]
    save_state(st)


if __name__ == "__main__":
    main()
