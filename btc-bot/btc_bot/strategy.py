"""
"Flow Regime" strategy -- long/flat by default (Alpaca crypto spot cannot be shorted).

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
    target_vol=0.5,     # annualised volatility target for the position (BTC itself runs ~50-70%)
    allow_short=False,
)


def signal(f, p):
    dev = f["flow_dev"]
    long_ok = dev > p["entry_dev"]
    short_ok = dev < -p["entry_dev"]
    if p.get("spot_confirm") and "spot_dev" in f:
        long_ok &= f["spot_dev"] > 0
        short_ok &= f["spot_dev"] < 0
    if p.get("trend_filter"):
        long_ok &= f["close"] > f["ema_slow"]
        short_ok &= f["close"] < f["ema_slow"]
    fz = p.get("funding_max_z")
    if fz is not None and "funding_z" in f:
        long_ok &= ~(f["funding_z"] > fz)
        short_ok &= ~(f["funding_z"] < -fz)
    out = {
        "entry_long": long_ok,
        "exit_long": dev < p["exit_dev"],
        "entry_short": short_ok,
        "exit_short": dev > -p["exit_dev"],
    }
    return {k: v.fillna(False).to_numpy() for k, v in out.items()}
