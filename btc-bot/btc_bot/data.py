"""
Historical data from Binance's public bulk archive (https://data.binance.vision).

Why Binance: its klines include *taker buy volume*, which lets us split every bar's
volume into aggressive buying vs aggressive selling -- the core "market flow" signal.
No API key needed. Files are cached under ./data so you only download once.

Datasets
  spot  klines  BTCUSDT  (price + spot taker flow)
  perp  klines  BTCUSDT  (USD-M perpetual taker flow -- where most BTC volume trades)
  perp  fundingRate      (8h funding: how crowded longs/shorts are)
"""
import io, os, zipfile, datetime as dt, urllib.request, urllib.error
import pandas as pd

BASE = "https://data.binance.vision/data"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, "data")

KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
              "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _read_zip_csv(raw, names=None):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        with z.open(z.namelist()[0]) as f:
            txt = f.read().decode()
    first = txt.split("\n", 1)[0]
    has_header = not first.split(",")[0].strip().lstrip("-").isdigit()
    df = pd.read_csv(io.StringIO(txt), header=0 if has_header else None)
    if names and len(df.columns) == len(names):
        df.columns = names          # header names vary between files; use position
    return df


def _months(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def _fetch_series(kind, dataset, symbol, interval, start, end, names):
    """Monthly archives, then daily archives for the current (incomplete) month."""
    prefix = f"{BASE}/{'spot' if kind == 'spot' else 'futures/um'}"
    sub = f"{dataset}/{symbol}" + (f"/{interval}" if interval else "")
    stem = f"{symbol}-{interval}" if interval else f"{symbol}-{dataset}"
    # BTCUSDT keeps its original cache path; other symbols get their own folder
    cdir = os.path.join(CACHE, *(() if symbol == "BTCUSDT" else (symbol,)), kind, dataset, interval or "")
    os.makedirs(cdir, exist_ok=True)
    frames, today = [], dt.date.today()
    for y, m in _months(start, end):
        tag = f"{y}-{m:02d}"
        path = os.path.join(cdir, f"{tag}.csv")
        current = (y, m) == (today.year, today.month)
        if os.path.exists(path) and not current:
            frames.append(pd.read_csv(path)); continue
        raw = None if current else _get(f"{prefix}/monthly/{sub}/{stem}-{tag}.zip")
        if raw is not None:
            df = _read_zip_csv(raw, names)
        else:   # month not archived yet -> stitch daily files (klines only)
            if not interval:
                continue
            days = []
            ddir = os.path.join(cdir, "daily"); os.makedirs(ddir, exist_ok=True)
            d = dt.date(y, m, 1)
            while d.month == m and d < today:
                dpath = os.path.join(ddir, f"{d.isoformat()}.csv")
                if os.path.exists(dpath):
                    days.append(pd.read_csv(dpath))
                else:
                    r = _get(f"{prefix}/daily/{sub}/{stem}-{d.isoformat()}.zip")
                    if r is not None:      # usually published the following day
                        dd = _read_zip_csv(r, names); dd.to_csv(dpath, index=False); days.append(dd)
                d += dt.timedelta(days=1)
            if not days:
                continue
            df = pd.concat(days, ignore_index=True)
        if not current:
            df.to_csv(path, index=False)
        frames.append(df)
        if not current:
            print(f"  downloaded {kind} {dataset} {tag}: {len(df)} rows")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _to_ts(s):
    s = pd.to_numeric(s)
    # Binance switched spot timestamps to microseconds in 2025
    unit = pd.Series("ms", index=s.index).where(s < 1e14, "us")
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns, UTC]")
    for u in ("ms", "us"):
        m = unit == u
        if m.any():
            out[m] = pd.to_datetime(s[m], unit=u, utc=True)
    return out


def load_klines(kind="perp", interval="1h", start="2019-10-01", end=None, symbol="BTCUSDT"):
    start = pd.Timestamp(start).date()
    end = pd.Timestamp(end).date() if end else dt.date.today()
    df = _fetch_series(kind, "klines", symbol, interval, start, end, KLINE_COLS)
    df = df[KLINE_COLS[:11]].copy()
    df.index = _to_ts(df["open_time"])
    df = df.drop(columns=["open_time", "close_time"]).astype(float)
    df = df[~df.index.duplicated()].sort_index()
    return df.loc[str(start):str(end)]


def load_funding(start="2019-10-01", end=None, symbol="BTCUSDT"):
    start = pd.Timestamp(start).date()
    end = pd.Timestamp(end).date() if end else dt.date.today()
    df = _fetch_series("perp", "fundingRate", symbol, None, start, end,
                       ["calc_time", "funding_interval_hours", "last_funding_rate"])
    df.index = _to_ts(df["calc_time"])
    s = df["last_funding_rate"].astype(float)
    return s[~s.index.duplicated()].sort_index()


def load_dataset(interval="1h", start="2019-10-01", end=None):
    """Perp OHLCV + perp taker flow + spot taker flow + funding, aligned on one index."""
    perp = load_klines("perp", interval, start, end)
    spot = load_klines("spot", interval, start, end)
    fund = load_funding(start, end)
    df = perp[["open", "high", "low", "close", "volume", "quote_volume", "trades", "taker_buy_base"]].copy()
    df["spot_volume"] = spot["volume"].reindex(df.index)
    df["spot_taker_buy"] = spot["taker_buy_base"].reindex(df.index)
    df["spot_close"] = spot["close"].reindex(df.index)
    # funding known at its timestamp -> forward-fill onto bars (no lookahead)
    df["funding"] = fund.reindex(df.index.union(fund.index)).ffill().reindex(df.index)
    return df.dropna(subset=["close"])


if __name__ == "__main__":
    d = load_dataset()
    print(d.tail()); print(len(d), "bars", d.index[0], "->", d.index[-1])
