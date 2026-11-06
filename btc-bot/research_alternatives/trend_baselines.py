"""Plain trend-following baselines vs the Flow Regime strategy. Verdict: trend-only REJECTED (collapses out-of-sample).
Run from btc-bot/:  python research_alternatives/trend_baselines.py
See STRATEGY_LOG.md for the recorded results."""
import sys; import os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, pandas as pd
from btc_bot import data, features, strategy, backtest as bt
raw=data.load_dataset(); df=features.resample(raw,"4h")
P={**strategy.DEFAULTS,**strategy.PROFILES["spot_long_flat"]}; f=features.build(df,P)
C=bt.Costs(25,5)
def trend_sig(f, adx=True, sma=None):
    up = f.close > (f.close.rolling(sma).mean() if sma else f.ema_slow)
    if adx: up &= f.adx >= 20
    up=up.fillna(False).to_numpy(); z=np.zeros(len(f),bool)
    return {"entry_long":up,"exit_long":~up,"entry_short":z,"exit_short":z}
def rnd_sig(f, seed):
    # random entries at same frequency as flow entries, inside BULL regime, same exits/stops
    s=strategy.signal(f,P); rng=np.random.default_rng(seed); reg=(strategy.regime(f,P)=="BULL").to_numpy()
    rate=s["entry_long"].mean()/max(reg.mean(),1e-9)
    e=reg & (rng.random(len(f))<rate); ex=rng.random(len(f))<s["exit_long"].mean()
    z=np.zeros(len(f),bool); return {"entry_long":e,"exit_long":ex,"entry_short":z,"exit_short":z}
variants={
 "v1.0 flow strategy": lambda ff: strategy.signal(ff,P),
 "trend only: above EMA200 + ADX>=20": lambda ff: trend_sig(ff),
 "trend only: above EMA200": lambda ff: trend_sig(ff,adx=False),
 "trend only: above 1200-bar SMA (~200 day)": lambda ff: trend_sig(ff,adx=False,sma=1200),
}
SL=[("IS",slice("2020-06-01","2022-12-31")),("OOS",slice("2023-01-01",None)),("2022",slice("2022-01-01","2022-12-31")),("FULL",slice("2020-06-01",None))]
for name,fn in variants.items():
    o=[]
    for nm,sl in SL:
        ff=f.loc[sl]; s=bt.run(ff,fn(ff),P,costs=C).stats
        o.append(f"{nm} S={s['sharpe']:.2f} cagr={s['cagr']:+.0%} dd={s['max_dd']:.0%} n={s['trades']}")
    print(f"{name:42s} | "+" | ".join(o))
