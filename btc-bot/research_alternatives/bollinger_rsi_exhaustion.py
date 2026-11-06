"""Bollinger RSI Exhaustion (BB 20/2.5, RSI7 <20/>80, target = basis, stop 1.5 ATR, 1.5% risk) on BTC 1h/4h. Verdict: REJECTED.
Run from btc-bot/:  python research_alternatives/bollinger_rsi_exhaustion.py
See STRATEGY_LOG.md for the recorded results."""
import sys; import os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, pandas as pd
from btc_bot import data, features
raw=data.load_dataset()
def rsi(c,n):
    d=c.diff(); up=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean(); dn=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean()
    return 100-100/(1+up/dn)
def sim(df, fee, slip, tp_fee, long_only=False, risk=0.015, max_lev=1.0, bb_k=2.5, same_bar="stop"):
    c,o,h,l=(df[k].to_numpy() for k in ("close","open","high","low"))
    basis=df.close.rolling(20).mean(); sd=df.close.rolling(20).std(ddof=0)
    up,lo=(basis+bb_k*sd).to_numpy(),(basis-bb_k*sd).to_numpy(); b=basis.to_numpy()
    r=rsi(df.close,7).to_numpy(); a=features.atr(df,14).to_numpy()
    eq=1.0; side=0; trades=[]; curve=np.empty(len(df)); pend=0
    for j in range(len(df)):
        if pend and side==0 and np.isfinite(a[j-1]):
            px=o[j]*(1+slip*pend); dist=1.5*a[j-1]
            frac=min(risk*eq/(dist/px*eq)*1, max_lev) if dist>0 else 0   # fraction of equity as notional
            notional=frac*eq; qty=notional/px; eq-=notional*fee
            side, entry, stop, q0, eq0 = pend, px, px-pend*dist, qty, eq+notional*fee
        pend=0
        if side!=0:
            tp=b[j-1]   # resting limit at the basis known at the previous close
            hit_stop = (l[j]<=stop) if side>0 else (h[j]>=stop)
            hit_tp = (h[j]>=tp) if side>0 else (l[j]<=tp)
            if side>0 and o[j]>=tp: hit_tp, xpx = True, o[j]
            if side<0 and o[j]<=tp: hit_tp, xpx = True, o[j]
            exitpx=None
            if hit_stop and (not hit_tp or same_bar=="stop"):
                exitpx=(min(o[j],stop) if side>0 else max(o[j],stop))*(1-slip*side); f=fee; why="stop"
            elif hit_tp:
                exitpx = (o[j] if ((side>0 and o[j]>=tp) or (side<0 and o[j]<=tp)) else tp); f=tp_fee; why="target"
            if exitpx is not None:
                pnl=side*q0*(exitpx-entry)-q0*exitpx*f; eq+=pnl
                trades.append(dict(t=df.index[j],ret=eq/eq0-1,why=why)); side=0
        curve[j]=eq+(side*q0*(c[j]-entry) if side else 0)
        if side==0 and j>0:
            if c[j]<lo[j] and r[j]<20: pend=1
            elif not long_only and c[j]>up[j] and r[j]>80: pend=-1
    return pd.Series(curve,index=df.index), pd.DataFrame(trades)
def report(label, df, **kw):
    out=[]
    for nm,sl in [("IS 2020-22",slice("2020-06-01","2022-12-31")),("OOS 2023-26",slice("2023-01-01",None))]:
        e,t=sim(df.loc[sl],**kw)
        d=e.resample("1D").last(); rr=d.pct_change().dropna(); yrs=(e.index[-1]-e.index[0]).days/365.25
        w=t.ret[t.ret>0]; ls=t.ret[t.ret<=0]
        out.append(f"{nm}: n={len(t)} win={(t.ret>0).mean():.0%} avgW={w.mean():+.2%} avgL={ls.mean():+.2%} CAGR={(e.iloc[-1]/e.iloc[0])**(1/yrs)-1:+.1%} Sharpe={rr.mean()/rr.std()*np.sqrt(365):.2f} DD={(e/e.cummax()-1).min():.1%}")
    print(label); [print("   ",x) for x in out]
h1=raw[["open","high","low","close"]].dropna()
report("1H as written, long+short, perp costs (taker 5bp+3bp slip, target limit 2bp maker), max 1x", h1, fee=0.0005, slip=0.0003, tp_fee=0.0002)
report("1H as written, long+short, perp costs, leverage allowed up to 3x (1.5% risk)", h1, fee=0.0005, slip=0.0003, tp_fee=0.0002, max_lev=3)
report("1H long-only on Alpaca (0.25% taker +5bp, target as limit 0.15% maker), max 1x", h1, fee=0.0025, slip=0.0005, tp_fee=0.0015, long_only=True)
report("1H perp costs, ZERO fees and slippage (best case, shows raw edge)", h1, fee=0, slip=0, tp_fee=0)
report("1H zero costs, optimistic same-bar rule (target before stop)", h1, fee=0, slip=0, tp_fee=0, same_bar="target")
h4=features.resample(raw,"4h")[["open","high","low","close"]]
report("4H as written, perp costs", h4, fee=0.0005, slip=0.0003, tp_fee=0.0002)
