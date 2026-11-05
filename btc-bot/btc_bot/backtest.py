"""
Bar-by-bar backtester with no lookahead.

Timeline for each bar j:
  1. at OPEN of j: execute orders decided at the close of j-1 (entries/exits), paying fee + slippage
  2. during j:     protective stop hit if low <= stop (long) -> fill at min(open, stop) minus slippage
                   (a gap through the stop fills at the open, i.e. worse than the stop)
  3. at CLOSE of j: mark equity to market, ratchet the trailing stop, read signals for bar j+1

Position size = risk_per_trade * equity / stop distance, capped at max_leverage * equity.
"""
from dataclasses import dataclass, field
import numpy as np
import pandas as pd


@dataclass
class Costs:
    fee_bps: float = 25.0       # per side. Alpaca crypto taker fee at the base tier is 0.25%
    slippage_bps: float = 5.0   # per side, on top of the fee
    funding: bool = False       # perp venue: longs pay / shorts receive the 8h funding rate


@dataclass
class Result:
    equity: pd.Series
    trades: pd.DataFrame
    stats: dict = field(default_factory=dict)


def position_fraction(p, atr_px, price, rvol_bar, risk_per_trade, max_leverage, bars_per_year):
    """Fraction of equity to deploy. 'vol' = volatility targeting, 'risk' = fixed % risk to the stop."""
    if p.get("sizing", "vol") == "vol":
        ann = rvol_bar * np.sqrt(bars_per_year)
        frac = p.get("target_vol", 0.5) / ann if ann > 0 else 0.0
    else:
        frac = risk_per_trade * price / (p["stop_atr"] * atr_px)
    return float(min(max(frac, 0.0), max_leverage))


def run(f, sig, p, costs=Costs(), risk_per_trade=0.02, max_leverage=1.0, start_equity=10_000.0):
    o, h, l, c, a, rv = (f[k].to_numpy() for k in ("open", "high", "low", "close", "atr", "rvol"))
    idx = f.index
    bpy = pd.Timedelta(days=365) / (idx[1] - idx[0])
    fr = (f["funding"].fillna(0).to_numpy() * ((idx[1] - idx[0]) / pd.Timedelta(hours=8))
          if costs.funding and "funding" in f else np.zeros(len(f)))
    n = len(f)
    fee, slip = costs.fee_bps / 1e4, costs.slippage_bps / 1e4
    cash, qty, side = start_equity, 0.0, 0          # side: +1 long, -1 short, 0 flat
    stop = entry_px = entry_eq = risk_usd = 0.0
    extreme = 0.0                                    # best close since entry, for trailing
    entry_t = None
    pend = 0                                         # +1 open long, -1 open short, 2 close
    eq = np.empty(n)
    trades = []

    def close_pos(px_raw, t, why):
        nonlocal cash, qty, side, entry_t
        px = px_raw * (1 - slip * side)
        cash += side * qty * px - qty * px * fee
        pnl = cash - entry_eq
        trades.append(dict(entry_time=entry_t, exit_time=t, side="long" if side > 0 else "short",
                           entry=entry_px, exit=px, qty=qty, pnl=pnl, ret=pnl / entry_eq,
                           r=pnl / risk_usd if risk_usd else np.nan, reason=why))
        qty, side, entry_t = 0.0, 0, None

    for j in range(n):
        # 1) execute pending order at the open
        if pend == 2 and side != 0:
            close_pos(o[j], idx[j], "signal")
        elif pend in (1, -1) and side == 0 and np.isfinite(a[j - 1]) and a[j - 1] > 0:
            equity = cash
            dist = p["stop_atr"] * a[j - 1]
            px = o[j] * (1 + slip * pend)
            frac = position_fraction(p, a[j - 1], o[j], rv[j - 1], risk_per_trade, max_leverage, bpy)
            q = frac * equity / px
            cash -= pend * q * px + q * px * fee
            qty, side, entry_px, entry_eq, entry_t = q, pend, px, equity, idx[j]
            risk_usd = q * dist
            stop = px - pend * dist
            extreme = px
        pend = 0
        # 2) protective stop inside the bar
        if side > 0 and l[j] <= stop:
            close_pos(min(o[j], stop), idx[j], "stop")
        elif side < 0 and h[j] >= stop:
            close_pos(max(o[j], stop), idx[j], "stop")
        # 3) funding accrual, then mark to market at the close
        if side != 0 and fr[j]:
            cash -= side * qty * c[j] * fr[j]
        eq[j] = cash + side * qty * c[j]
        if j == n - 1:
            break
        if side > 0:
            extreme = max(extreme, c[j])
            stop = max(stop, extreme - p["trail_atr"] * a[j])
            if sig["exit_long"][j]:
                pend = 2
        elif side < 0:
            extreme = min(extreme, c[j])
            stop = min(stop, extreme + p["trail_atr"] * a[j])
            if sig["exit_short"][j]:
                pend = 2
        else:
            if sig["entry_long"][j]:
                pend = 1
            elif p.get("allow_short") and sig["entry_short"][j]:
                pend = -1
    if side != 0:   # close at the final bar for accounting
        close_pos(c[-1], idx[-1], "end")
        eq[-1] = cash
    equity = pd.Series(eq, index=idx)
    tr = pd.DataFrame(trades)
    return Result(equity, tr, stats(equity, tr))


def stats(equity, tr, bars_per_year=None):
    daily = equity.resample("1D").last().dropna()
    r = daily.pct_change().dropna()
    yrs = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    dd = equity / equity.cummax() - 1
    sharpe = r.mean() / r.std() * np.sqrt(365) if r.std() > 0 else 0.0
    down = r[r < 0].std()
    sortino = r.mean() / down * np.sqrt(365) if down and down > 0 else 0.0
    out = dict(total_return=equity.iloc[-1] / equity.iloc[0] - 1, cagr=cagr, sharpe=sharpe,
               sortino=sortino, max_dd=dd.min(), calmar=cagr / abs(dd.min()) if dd.min() < 0 else np.nan,
               trades=len(tr))
    if len(tr):
        wins, losses = tr.pnl[tr.pnl > 0], tr.pnl[tr.pnl <= 0]
        held = (tr.exit_time - tr.entry_time).dt.total_seconds().sum()
        out.update(win_rate=len(wins) / len(tr),
                   profit_factor=wins.sum() / -losses.sum() if losses.sum() < 0 else np.inf,
                   avg_r=tr.r.mean(), avg_trade=tr.ret.mean(),
                   exposure=held / (equity.index[-1] - equity.index[0]).total_seconds(),
                   avg_hold_days=held / len(tr) / 86400)
    return out


def buy_and_hold(f, costs=Costs(), start_equity=10_000.0):
    px0 = f["open"].iloc[0] * (1 + costs.slippage_bps / 1e4)
    q = start_equity / (px0 * (1 + costs.fee_bps / 1e4))
    eq = pd.Series(q * f["close"].to_numpy(), index=f.index)
    return Result(eq, pd.DataFrame(), stats(eq, pd.DataFrame()))
