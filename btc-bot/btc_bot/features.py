"""
Feature engineering. Every feature at bar t uses only data available at the CLOSE of bar t.

Market-flow features (from taker buy volume):
  delta        = taker_buy - taker_sell  (aggressive buyers minus aggressive sellers, in BTC)
  flow_imb     = sum(delta, n) / sum(volume, n)       -> [-1, +1] imbalance over n bars
  flow_z       = z-score of flow_imb vs its own trailing history (adapts to regime)
  cvd_slope    = change in cumulative volume delta over n bars, scaled by volume
  spot_lead    = spot flow imbalance - perp flow imbalance (spot-driven buying is "real" demand,
                 perp-driven buying is often leverage)
Positioning:
  funding_z    = z-score of the funding rate (crowded longs -> high positive funding)
Price / risk:
  ema_fast/slow, trend, atr, realized vol, Donchian channels
"""
import numpy as np
import pandas as pd


def resample(df, rule="4h"):
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
           "quote_volume": "sum", "trades": "sum", "taker_buy_base": "sum",
           "spot_volume": "sum", "spot_taker_buy": "sum", "spot_close": "last", "funding": "last"}
    agg = {k: v for k, v in agg.items() if k in df.columns}
    return df.resample(rule, label="left", closed="left").agg(agg).dropna(subset=["close"])


def zscore(s, n):
    m = s.rolling(n, min_periods=n // 2).mean()
    sd = s.rolling(n, min_periods=n // 2).std()
    return (s - m) / sd.replace(0, np.nan)


def atr(df, n=14):
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def adx(df, n=14):
    up, dn = df["high"].diff(), -df["low"].diff()
    pdm = up.where((up > dn) & (up > 0), 0.0)
    mdm = dn.where((dn > up) & (dn > 0), 0.0)
    a = atr(df, n)
    pdi = 100 * pdm.ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * mdm.ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def build(df, p):
    """p: dict of feature params (see strategy.DEFAULTS)."""
    f = pd.DataFrame(index=df.index)
    f["open"], f["high"], f["low"], f["close"] = df["open"], df["high"], df["low"], df["close"]
    vol = df["volume"]
    delta = 2 * df["taker_buy_base"] - vol
    n = p["flow_n"]
    f["flow_imb"] = delta.rolling(n).sum() / vol.rolling(n).sum()
    f["flow_z"] = zscore(f["flow_imb"], p["z_n"])
    # deviation from the trailing ~1y median: perp flow has a structural sell-side bias
    base_n = p.get("base_n", 2190)
    f["flow_dev"] = f["flow_imb"] - f["flow_imb"].rolling(base_n, min_periods=base_n // 4).median()
    cvd = delta.cumsum()
    f["cvd_slope"] = (cvd - cvd.shift(n)) / vol.rolling(p["z_n"]).mean() / n
    if "spot_taker_buy" in df:
        sdelta = 2 * df["spot_taker_buy"] - df["spot_volume"]
        f["spot_imb"] = sdelta.rolling(n).sum() / df["spot_volume"].rolling(n).sum()
        f["spot_z"] = zscore(f["spot_imb"], p["z_n"])
        f["spot_dev"] = f["spot_imb"] - f["spot_imb"].rolling(base_n, min_periods=base_n // 4).median()
    if "funding" in df:
        f["funding"] = df["funding"]
        f["funding_z"] = zscore(df["funding"], p["z_n"])
    f["ema_fast"] = df["close"].ewm(span=p["ema_fast"], adjust=False).mean()
    f["ema_slow"] = df["close"].ewm(span=p["ema_slow"], adjust=False).mean()
    f["atr"] = atr(df, p["atr_n"])
    r = np.log(df["close"]).diff()
    f["rvol"] = r.rolling(p["z_n"]).std()
    f["rvol_rank"] = f["rvol"].rolling(p["z_n"] * 3, min_periods=p["z_n"]).rank(pct=True)
    # --- regime / consolidation measures
    er_n = p.get("er_n", 42)
    chg = df["close"].diff().abs()
    f["er"] = (df["close"] - df["close"].shift(er_n)).abs() / chg.rolling(er_n).sum()   # Kaufman efficiency ratio
    f["adx"] = adx(df, p.get("adx_n", 14))
    f["ema_slope"] = f["ema_slow"].pct_change(p.get("slope_n", 30))
    bw = (df["high"].rolling(er_n).max() - df["low"].rolling(er_n).min()) / df["close"]
    f["range_rank"] = bw.rolling(p.get("base_n", 2190), min_periods=p.get("base_n", 2190) // 4).rank(pct=True)   # range width vs past year
    f["don_hi"] = df["high"].rolling(p["don_n"]).max()
    f["don_lo"] = df["low"].rolling(p["don_n"]).min()
    return f
