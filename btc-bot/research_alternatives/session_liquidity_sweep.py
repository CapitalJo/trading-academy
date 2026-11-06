"""Session Liquidity Sweep (Asian range 00-07 UTC; London 07-09 / NY 12-14 UTC sweep-and-reclaim on 1m candles;
stop $20 beyond the sweep wick; 50% off at range midpoint, rest at the opposite boundary; 1% risk; stop for the day
after 2 consecutive losses). Tested on BTCUSDT perpetual 1-minute data, long + short (perp venue).
Run from btc-bot/:  python research_alternatives/session_liquidity_sweep.py     See STRATEGY_LOG.md for recorded results."""
import io, os, sys, zipfile, urllib.request, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, "data", "BTCUSDT_1m")
URL = "https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-{}.zip"


def month_file(tag):
    path = os.path.join(CACHE, f"{tag}.npz")
    if not os.path.exists(path):
        with urllib.request.urlopen(URL.format(tag), timeout=120) as r:
            raw = r.read()
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            txt = z.open(z.namelist()[0]).read().decode()
        df = pd.read_csv(io.StringIO(txt), header=None)
        if not str(df.iloc[0, 0]).isdigit():
            df = df.iloc[1:]
        a = df.iloc[:, :5].astype(float).to_numpy()
        np.savez_compressed(path, t=a[:, 0].astype(np.int64), ohlc=a[:, 1:5].astype(np.float64))
    return path


def load_1m(start="2020-06", end=None):
    os.makedirs(CACHE, exist_ok=True)
    end = end or (dt.date.today().replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
    tags = [p.strftime("%Y-%m") for p in pd.period_range(start, end, freq="M")]
    with ThreadPoolExecutor(8) as ex:
        paths = list(ex.map(month_file, tags))
    ts, ohlc = [], []
    for p in paths:
        z = np.load(p); ts.append(z["t"]); ohlc.append(z["ohlc"])
    t = np.concatenate(ts); x = np.concatenate(ohlc)
    t = np.where(t > 1e14, t // 1000, t)          # microsecond timestamps -> ms
    return pd.DataFrame(x, columns=["open", "high", "low", "close"], index=pd.to_datetime(t, unit="ms", utc=True))


def simulate(m, fee=0.0005, slip=0.0002, tp_fee=0.0002, risk=0.01, max_lev=5.0, stop_buf=20.0,
             windows=((7, 9), (12, 14)), weekdays_only=False, be_after_tp1=False, max_losses=2, recl_n=5):
    """Returns (daily equity Series, trades DataFrame). Conservative: if stop and target hit in one minute, stop first."""
    eq = 1.0
    out_eq, trades = {}, []
    for day, d in m.groupby(m.index.floor("D")):
        if weekdays_only and day.weekday() >= 5:
            out_eq[day] = eq; continue
        mins = (d.index.hour * 60 + d.index.minute).to_numpy()
        o, h, l, c = (d[k].to_numpy() for k in ("open", "high", "low", "close"))
        asia = mins < 7 * 60
        if asia.sum() < 300:
            out_eq[day] = eq; continue
        hi, lo = h[asia].max(), l[asia].min(); mid = (hi + lo) / 2
        in_win = np.zeros(len(d), bool)
        for a, b in windows: in_win |= (mins >= a * 60) & (mins < b * 60)
        losses, i, n = 0, int(asia.sum()), len(d)
        sweep_dn = sweep_up = None                      # index where the sweep started
        while i < n and losses < max_losses:
            if not in_win[i]:
                sweep_dn = sweep_up = None; i += 1; continue
            side = 0
            if l[i] < lo and sweep_dn is None: sweep_dn = i
            if h[i] > hi and sweep_up is None: sweep_up = i
            if sweep_dn is not None:
                if c[i] > lo and i > sweep_dn - 1 and i - sweep_dn < recl_n and l[sweep_dn:i + 1].min() < lo:
                    side, wick = 1, l[sweep_dn:i + 1].min()
                elif i - sweep_dn >= recl_n: sweep_dn = None
            if side == 0 and sweep_up is not None:
                if c[i] < hi and i - sweep_up < recl_n and h[sweep_up:i + 1].max() > hi:
                    side, wick = -1, h[sweep_up:i + 1].max()
                elif i - sweep_up >= recl_n: sweep_up = None
            if side == 0 or i + 1 >= n:
                i += 1; continue
            # enter at next minute's open (the reclaim candle's close is only known when it ends)
            j = i + 1
            entry = o[j] * (1 + slip * side)
            stop = wick - side * stop_buf
            tp1, tp2 = mid, (hi if side > 0 else lo)
            dist = side * (entry - stop)
            if dist <= 0 or side * (tp1 - entry) <= 0:  # already past the midpoint: no trade
                sweep_dn = sweep_up = None; i = j; continue
            notional = min(risk * eq / (dist / entry), max_lev * eq)
            q = notional / entry
            eq0 = eq; eq -= notional * fee
            rem, k, why = 1.0, j, "eod"
            while k < n:
                hit_stop = (l[k] <= stop) if side > 0 else (h[k] >= stop)
                if hit_stop:
                    px = (min(o[k], stop) if side > 0 else max(o[k], stop)) * (1 - slip * side)
                    eq += side * q * rem * (px - entry) - q * rem * px * fee; rem = 0; why = "stop" if why != "tp1" else "tp1+stop"; break
                if rem == 1.0 and ((h[k] >= tp1) if side > 0 else (l[k] <= tp1)):
                    eq += side * q * 0.5 * (tp1 - entry) - q * 0.5 * tp1 * tp_fee; rem = 0.5; why = "tp1"
                    if be_after_tp1: stop = entry
                if rem == 0.5 and ((h[k] >= tp2) if side > 0 else (l[k] <= tp2)):
                    eq += side * q * 0.5 * (tp2 - entry) - q * 0.5 * tp2 * tp_fee; rem = 0; why = "tp2"; break
                k += 1
            if rem > 0:                                 # close at end of day
                px = c[n - 1] * (1 - slip * side)
                eq += side * q * rem * (px - entry) - q * rem * px * fee
            r = eq / eq0 - 1
            trades.append(dict(day=day, side=side, ret=r, R=r / risk, why=why, stop_pct=dist / entry))
            losses = losses + 1 if r <= 0 else 0
            sweep_dn = sweep_up = None
            i = k + 1
        out_eq[day] = eq
    return pd.Series(out_eq), pd.DataFrame(trades)


def summarize(label, eqs, tr):
    out = []
    for nm, a, b in [("2020-06→2022", "2020-06-01", "2022-12-31"), ("2023→now", "2023-01-01", None)]:
        e = eqs.loc[a:b]; t = tr[(tr.day >= pd.Timestamp(a, tz="UTC")) & ((tr.day <= pd.Timestamp(b, tz="UTC")) if b else True)]
        e = e / e.iloc[0]; r = e.pct_change().dropna(); yrs = (e.index[-1] - e.index[0]).days / 365.25
        out.append(f"{nm}: n={len(t)} win={(t.ret > 0).mean():.0%} avgR={t.R.mean():+.2f} "
                   f"avgW={t.R[t.R > 0].mean():+.2f}R avgL={t.R[t.R <= 0].mean():+.2f}R "
                   f"CAGR={e.iloc[-1] ** (1 / yrs) - 1:+.1%} Sharpe={r.mean() / r.std() * np.sqrt(365):.2f} DD={(e / e.cummax() - 1).min():.1%}")
    print(label); [print("    " + x) for x in out]


def main():
    m = load_1m()
    print(f"loaded {len(m):,} one-minute bars {m.index[0]} → {m.index[-1]}")
    eq, tr = simulate(m)
    summarize("AS WRITTEN (perp taker 5bp + 2bp slip, targets as 2bp limit orders, max 5x leverage)", eq, tr)
    print(f"    median stop distance {tr.stop_pct.median():.2%} of price; exits: {tr.why.value_counts().to_dict()}")
    summarize("Same, ZERO fees and slippage (raw edge)", *simulate(m, fee=0, slip=0, tp_fee=0))
    summarize("Same, weekdays only", *simulate(m, weekdays_only=True))
    summarize("Same, stop to breakeven after the 50% partial", *simulate(m, be_after_tp1=True))
    summarize("London window only", *simulate(m, windows=((7, 9),)))
    summarize("New York window only", *simulate(m, windows=((12, 14),)))
    summarize("Alpaca-level costs (0.25% taker + 5bp slip, 0.15% maker targets), long only not modelled", *simulate(m, fee=0.0025, slip=0.0005, tp_fee=0.0015))


if __name__ == "__main__":
    main()
