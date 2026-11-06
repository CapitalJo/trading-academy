"""
"Flow Regime" strategy -- trades bull and bear regimes, pauses in consolidation.

Regimes (computed at each 4h close):
  BULL   price above its 200-bar EMA and ADX(14) >= 20          -> long when buy-side flow confirms
  BEAR   price below the EMA, EMA falling, and ADX >= 20       -> short when sell-side flow confirms
                                                                   (cash instead on venues that can't short)
  RANGE  anything else (ADX < 20 = consolidation)              -> no new entries; open trades keep their
                                                                   normal flow/stop exits

Research behind it (see research.py / REPORT.md):
  * Multi-day taker-flow imbalance (aggressive buy volume minus aggressive sell volume, divided
    by total volume) has a small but *persistent* positive correlation with forward 1-20 day
    returns, both in 2020-22 and out-of-sample in 2023-26.
  * Perp flow carries a structural sell-side bias, so we measure it relative to its own trailing
    1-year median ("flow_dev") -- adaptive and lookahead-free.
  * Crowded positioning (very high funding z-score) mildly predicts *lower* returns -> veto entries.
  * Short holding periods get eaten by fees (~0.6% round trip on Alpaca), so the strategy holds
    for days-to-weeks and uses hysteresis (enter above entry_dev, exit below exit_dev).

signal(f, p) returns per-bar booleans computed at bar CLOSE (acted on at the next bar's open):
  entry_long, exit_long, entry_short, exit_short
Protective stops (initial + chandelier trail, in ATRs) are applied by the backtester / live bot.
"""
DEFAULTS = dict(
    timeframe="4h",
    flow_n=84,          # bars summed for flow imbalance (84 x 4h = 14 days)
    base_n=2190,        # trailing window for the flow baseline (~1 year of 4h bars)
    z_n=180,            # window for z-scores (30 days)
    ema_fast=50, ema_slow=200,
    atr_n=14, don_n=20,
    entry_dev=0.005,    # enter when 14d flow is 0.5% of volume more buy-side than its 1y norm
    exit_dev=-0.005,    # exit when it is 0.5% more sell-side than norm
    spot_confirm=True,  # also require spot flow above its norm
    trend_filter=True,  # also require close > EMA(ema_slow)
    funding_max_z=None, # veto entries when funding z > this (tested: no benefit, so off)
    stop_atr=5.0,       # initial protective stop (ATRs below entry) -- a disaster stop
    trail_atr=8.0,      # chandelier trailing stop (ATRs below best close)
    sizing="vol",       # "vol": scale exposure to hit target_vol; "risk": fixed % of equity at risk
    target_vol=0.3,     # annualised volatility target for the position (BTC itself runs ~50-70%).
                        # 0.3 chosen for drawdown: same Sharpe as 0.5 with ~2/3 of the drawdown (SIGNOFF.md)
    allow_short=False,  # set by the venue profile (see PROFILES)
    # consolidation pause (None = off): block NEW entries when the market is range-bound
    chop_adx_min=20,    # require ADX(14) >= this (ADX < 20 = range-bound -> pause)
    chop_er_min=None,   # require 7-day efficiency ratio >= this
    # bear side (only used when allow_short=True)
    short_entry_dev=None,   # flow threshold for shorts (defaults to entry_dev)
    short_exit_dev=None,    # defaults to exit_dev
    short_slope=True,       # also require EMA(ema_slow) to be falling (stops shorting bull-market dips)
    chop_exit=False,        # also EXIT open positions in a range (tested: hurts, so off)
)

VERSION = "1.0"


def fingerprint(p=None):
    """Short hash of the exact parameter set. The bot only trades a fingerprint you signed off."""
    import hashlib, json
    p = DEFAULTS if p is None else p
    return hashlib.sha256(json.dumps(p, sort_keys=True, default=str).encode()).hexdigest()[:12]


# Venue profiles. Spot venues (Alpaca) can't short: bear regime = cash. Perp/futures venues trade both ways.
PROFILES = {
    "spot_long_flat": dict(allow_short=False),
    "perp_long_short": dict(allow_short=True),
}


def regime(f, p):
    """Label each bar BULL / BEAR / RANGE using the same rules the entries use."""
    import numpy as np
    import pandas as pd
    trending = f["adx"] >= p["chop_adx_min"] if p.get("chop_adx_min") is not None else True
    bull = (f["close"] > f["ema_slow"]) & trending
    bear = (f["close"] < f["ema_slow"]) & trending
    if p.get("short_slope"):
        bear &= f["ema_slope"] < 0
    return pd.Series(np.select([bull, bear], ["BULL", "BEAR"], "RANGE"), index=f.index)


def signal(f, p):
    dev = f["flow_dev"]
    se = p.get("short_entry_dev") if p.get("short_entry_dev") is not None else p["entry_dev"]
    sx = p.get("short_exit_dev") if p.get("short_exit_dev") is not None else p["exit_dev"]
    long_ok = dev > p["entry_dev"]
    short_ok = dev < -se
    if p.get("spot_confirm") and "spot_dev" in f:
        long_ok &= f["spot_dev"] > 0
        short_ok &= f["spot_dev"] < 0
    if p.get("trend_filter"):
        long_ok &= f["close"] > f["ema_slow"]
        short_ok &= f["close"] < f["ema_slow"]
    if p.get("short_slope"):
        short_ok &= f["ema_slope"] < 0
    trending = True
    if p.get("chop_adx_min") is not None:
        trending = trending & (f["adx"] >= p["chop_adx_min"])
    if p.get("chop_er_min") is not None:
        trending = trending & (f["er"] >= p["chop_er_min"])
    long_ok &= trending
    short_ok &= trending
    fz = p.get("funding_max_z")
    if fz is not None and "funding_z" in f:
        long_ok &= ~(f["funding_z"] > fz)
        short_ok &= ~(f["funding_z"] < -fz)
    chop = ~trending if trending is not True else False
    ce = p.get("chop_exit")
    out = {
        "entry_long": long_ok,
        "exit_long": (dev < p["exit_dev"]) | (chop if ce else False),
        "entry_short": short_ok,
        "exit_short": (dev > -sx) | (chop if ce else False),
    }
    return {k: v.fillna(False).to_numpy() for k, v in out.items()}
