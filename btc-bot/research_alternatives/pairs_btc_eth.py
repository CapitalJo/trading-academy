"""Pairs trading / statistical arbitrage: ETH vs BTC (log-price spread, rolling hedge ratio, z-score entries).
Includes an Engle-Granger cointegration (ADF) check, perp costs on all four legs, and funding on both legs.
Needs shorting -> perp venue only (cannot run on Alpaca crypto).
Run from btc-bot/:  python research_alternatives/pairs_btc_eth.py      See STRATEGY_LOG.md for recorded results."""
import itertools, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, pandas as pd
from btc_bot import data

FEE, SLIP = 0.0005, 0.0003          # per leg per side (perp taker + slippage)
IS, OOS = slice("2020-06-01", "2022-12-31"), slice("2023-01-01", None)


def adf_t(x, lags=1):
    """ADF t-statistic (constant, no trend) on series x."""
    x = np.asarray(x, float); dx = np.diff(x)
    y = dx[lags:]
    X = [np.ones_like(y), x[lags:-1]] + [dx[lags - k:-k] for k in range(1, lags + 1)]
    X = np.column_stack(X)
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ b
    s2 = resid @ resid / (len(y) - X.shape[1])
    cov = s2 * np.linalg.inv(X.T @ X)
    return b[1] / np.sqrt(cov[1, 1])


EG_CRIT = {"1%": -3.90, "5%": -3.34, "10%": -3.04}   # Engle-Granger, 2 variables, constant


def engle_granger(la, lb):
    beta, alpha = np.polyfit(lb, la, 1)
    return adf_t(la - beta * lb - alpha), beta


def load(rule):
    out = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        k = data.load_klines("perp", "1h", symbol=sym)["close"]
        fu = data.load_funding(symbol=sym)
        out[sym] = k.resample(rule).last()
        out[sym + "_f"] = fu.resample(rule).sum().reindex(out[sym].index).fillna(0)   # funding paid per bar
    return pd.DataFrame(out).dropna()


def backtest(d, L, entry, exit_z=0.0, stop=None, max_hold=None):
    la, lb = np.log(d.ETHUSDT), np.log(d.BTCUSDT)
    cov = la.rolling(L).cov(lb); var = lb.rolling(L).var()
    beta = cov / var
    alpha = la.rolling(L).mean() - beta * lb.rolling(L).mean()
    spread = la - beta * lb - alpha
    z = ((spread - spread.rolling(L).mean()) / spread.rolling(L).std()).to_numpy()
    bt_ = beta.to_numpy()
    n = len(d); pos = np.zeros(n); wE = np.zeros(n); wB = np.zeros(n)
    cur, held, b0 = 0, 0, 1.0
    for t in range(n):
        if not np.isfinite(z[t]) or not np.isfinite(bt_[t]):
            pos[t] = cur; continue
        if cur == 0:
            if z[t] > entry: cur, held, b0 = -1, 0, bt_[t]        # ETH rich: short ETH, long BTC
            elif z[t] < -entry: cur, held, b0 = 1, 0, bt_[t]
        else:
            held += 1
            if (cur == -1 and z[t] <= exit_z) or (cur == 1 and z[t] >= -exit_z) \
               or (stop and abs(z[t]) >= stop) or (max_hold and held >= max_hold):
                cur = 0
        pos[t] = cur
        g = 1 + abs(b0)                                           # gross exposure = 1x equity
        wE[t], wB[t] = cur / g, -cur * b0 / g
    rE, rB = d.ETHUSDT.pct_change().shift(-1).fillna(0).to_numpy(), d.BTCUSDT.pct_change().shift(-1).fillna(0).to_numpy()
    fE, fB = d.ETHUSDT_f.shift(-1).fillna(0).to_numpy(), d.BTCUSDT_f.shift(-1).fillna(0).to_numpy()
    gross = wE * rE + wB * rB - (wE * fE + wB * fB)               # longs pay funding, shorts receive
    cost = (np.abs(np.diff(np.r_[0, wE])) + np.abs(np.diff(np.r_[0, wB]))) * (FEE + SLIP)
    r = pd.Series(gross - cost, index=d.index)
    # trade-level returns
    tr, start, acc = [], None, 0.0
    for t in range(n):
        if pos[t] != 0 and (t == 0 or pos[t - 1] == 0): start, acc = t, 0.0
        if start is not None: acc = (1 + acc) * (1 + r.iloc[t]) - 1
        if start is not None and (t == n - 1 or pos[t] == 0):
            tr.append(acc); start = None
    return r, np.array(tr)


def stats(r, tr):
    eq = (1 + r).cumprod(); daily = eq.resample("1D").last().pct_change().dropna()
    yrs = (r.index[-1] - r.index[0]).days / 365.25
    return dict(cagr=eq.iloc[-1] ** (1 / yrs) - 1, sharpe=daily.mean() / daily.std() * np.sqrt(365) if daily.std() > 0 else 0,
                dd=(eq / eq.cummax() - 1).min(), n=len(tr), win=(tr > 0).mean() if len(tr) else np.nan,
                avgw=tr[tr > 0].mean() if (tr > 0).any() else np.nan, avgl=tr[tr <= 0].mean() if (tr <= 0).any() else np.nan)


def fmt(s): return (f"n={s['n']:3d} win={s['win']:.0%} avgW={s['avgw']:+.1%} avgL={s['avgl']:+.1%} "
                    f"CAGR={s['cagr']:+.1%} Sharpe={s['sharpe']:.2f} DD={s['dd']:.1%}")


def main():
    d1 = load("1D")
    la, lb = np.log(d1.ETHUSDT), np.log(d1.BTCUSDT)
    print("=== Cointegration (Engle-Granger ADF on log ETH vs log BTC, daily). Critical 5% = -3.34")
    for nm, sl in [("2020-06 → 2022", IS), ("2023 → now", OOS), ("full", slice("2020-06-01", None))]:
        t, b = engle_granger(la.loc[sl].to_numpy(), lb.loc[sl].to_numpy())
        print(f"  {nm:15s} ADF t = {t:6.2f}  hedge ratio {b:.2f}  -> {'COINTEGRATED' if t < EG_CRIT['5%'] else 'not cointegrated'}")
    ts = []
    for end in pd.date_range(pd.Timestamp("2021-01-01", tz="UTC"), d1.index[-1], freq="MS"):
        w = slice(end - pd.Timedelta(days=365), end)
        if len(la.loc[w]) > 300:
            ts.append(engle_granger(la.loc[w].to_numpy(), lb.loc[w].to_numpy())[0])
    ts = np.array(ts)
    print(f"  rolling 1-year windows: cointegrated (5%) in {(ts < EG_CRIT['5%']).mean():.0%} of {len(ts)} windows")

    for rule, bars_per_day in [("1D", 1), ("4h", 6)]:
        d = load(rule)
        print(f"\n=== Strategy on {rule} bars, perp costs on 4 legs + funding")
        spec = dict(L=60 * bars_per_day, entry=2.0)
        for nm, sl in [("IS", IS), ("OOS", OOS)]:
            print(f"  as described (60-day window, enter |z|>2, exit z=0, no stop) {nm}: " + fmt(stats(*backtest(d.loc[sl], **spec))))
        rows = []
        for Ld, e, st, mh in itertools.product([30, 60, 90, 120], [1.5, 2.0, 2.5], [None, 3.5], [None, 30]):
            p = dict(L=Ld * bars_per_day, entry=e, stop=st, max_hold=mh * bars_per_day if mh else None)
            a, b = stats(*backtest(d.loc[IS], **p)), stats(*backtest(d.loc[OOS], **p))
            rows.append((a["sharpe"], b["sharpe"], p, a, b))
        rows.sort(key=lambda x: -x[0])
        oos = np.array([x[1] for x in rows])
        print(f"  grid of {len(rows)}: OOS Sharpe > 0 in {(oos > 0).mean():.0%}; median OOS Sharpe {np.median(oos):.2f}")
        for s_is, s_oos, p, a, b in rows[:3]:
            print(f"  IS-best {p}:\n      IS  {fmt(a)}\n      OOS {fmt(b)}")


if __name__ == "__main__":
    main()
