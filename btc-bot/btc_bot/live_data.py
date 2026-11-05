"""
Recent bars for the live bot.

History comes from the bulk archive (btc_bot.data), which lags by up to ~1 day. We top it up with
the public REST APIs where reachable:
  spot  https://data-api.binance.vision/api/v3/klines   (market-data-only mirror, works from the US)
  perp  https://fapi.binance.com/fapi/v1/klines         (blocked in some regions, HTTP 451)
If perp REST is blocked, the perp flow simply comes from the archive (about 1 day stale). The
backtest shows the 14-day flow signal barely degrades with that lag (see REPORT.md section 5).
"""
import json, urllib.request, urllib.error
import pandas as pd
from . import data

SPOT_REST = "https://data-api.binance.vision/api/v3/klines"
PERP_REST = "https://fapi.binance.com/fapi/v1/klines"


def _rest_klines(url, interval="1h", limit=1000, symbol="BTCUSDT"):
    try:
        with urllib.request.urlopen(f"{url}?symbol={symbol}&interval={interval}&limit={limit}", timeout=20) as r:
            rows = json.loads(r.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None
    df = pd.DataFrame(rows, columns=data.KLINE_COLS)
    df.index = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
    return df[data.KLINE_COLS[1:11]].drop(columns=["close_time"]).astype(float)


def load_live(start="2024-06-01"):
    """Archive + REST top-up. Returns (hourly dataframe like data.load_dataset, info dict)."""
    hist = data.load_dataset(start=start)
    info = {"archive_last_bar": str(hist.index[-1])}
    spot = _rest_klines(SPOT_REST)
    perp = _rest_klines(PERP_REST)
    info["spot_rest"] = spot is not None
    info["perp_rest"] = perp is not None
    if spot is None:
        return hist, info
    new_idx = hist.index.union(spot.index)
    out = hist.reindex(new_idx)
    # spot columns always refreshed from REST
    out.loc[spot.index, "spot_volume"] = spot["volume"]
    out.loc[spot.index, "spot_taker_buy"] = spot["taker_buy_base"]
    out.loc[spot.index, "spot_close"] = spot["close"]
    if perp is not None:
        cols = ["open", "high", "low", "close", "volume", "quote_volume", "trades", "taker_buy_base"]
        out.loc[perp.index, cols] = perp[cols].to_numpy()
    else:
        # price columns from spot where perp archive hasn't caught up; perp flow stays as last known
        miss = out["close"].isna()
        for c in ["open", "high", "low", "close"]:
            out.loc[miss, c] = spot.loc[spot.index.intersection(out.index[miss]), c]
        info["perp_flow_stale_since"] = info["archive_last_bar"]
    out["funding"] = out["funding"].ffill()
    return out, info
