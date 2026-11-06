"""RSI pullback without flow, funding carry, and v1.0 + partial profit-take at several sizes.
Run from btc-bot/:  python research_alternatives/pullback_carry_profit_take.py
See STRATEGY_LOG.md for the recorded results."""
import sys, itertools; import os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, pandas as pd
from btc_bot import data, features, strategy, backtest as bt
raw=data.load_dataset(); df=features.resample(raw,"4h")
P={**strategy.DEFAULTS,**strategy.PROFILES["spot_long_flat"]}; f=features.build(df,P)
C=bt.Costs(25,5)
IS,OOS=slice("2020-06-01","2022-12-31"),slice("2023-01-01",None)
def rsi(c,n):
    d=c.diff(); up=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean(); dn=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean()
    return 100-100/(1+up/dn)
for n in (2,3,5): f[f"rsi{n}"]=rsi(f.close,n)
reg=strategy.regime(f,P); f["bull"]=reg=="BULL"
def mcdd(eq,n=3000,block=20,seed=3):
    r=eq.resample("1D").last().pct_change().dropna().to_numpy(); rng=np.random.default_rng(seed); out=[]
    for _ in range(n):
        st=rng.integers(0,len(r)-block,len(r)//block+1); s=np.concatenate([r[i:i+block] for i in st])[:len(r)]
        e=np.cumprod(1+s); out.append((e/np.maximum.accumulate(e)-1).min())
    return np.percentile(out,5)
def evaluate(fn, p, costs=C, label=""):
    out={}
    for nm,sl in [("IS",IS),("OOS",OOS),("FULL",slice("2020-06-01",None))]:
        ff=f.loc[sl]; r=bt.run(ff,fn(ff),p,costs=costs); out[nm]=r
    return out
def line(label,o):
    s=[f"{k} S={o[k].stats['sharpe']:.2f} cagr={o[k].stats['cagr']:+.0%} dd={o[k].stats['max_dd']:.0%} win={o[k].stats.get('win_rate',0):.0%} n={o[k].stats['trades']}" for k in ("IS","OOS")]
    print(f"{label:58s} | "+" | ".join(s)+f" | MC95 {mcdd(o['FULL'].equity):.0%}")

v1=lambda ff: strategy.signal(ff,P)
print("=== B2. Pullback without flow filter (classic dip-buy above trend)")
def pull2(rn,thr,ex):
    def fn(ff):
        z=np.zeros(len(ff),bool)
        e=((ff.close>ff.ema_slow) & (ff[f"rsi{rn}"]<thr)).fillna(False).to_numpy()
        x=(ff[f"rsi{rn}"]>ex).fillna(False).to_numpy()
        return {"entry_long":e,"exit_long":x,"entry_short":z,"exit_short":z}
    return fn
rows=[]
for rn,thr,ex,mh,st in itertools.product([2,3,5],[5,10,20],[50,65,80],[6,18,42],[3.0,5.0]):
    p={**P,"max_hold":mh,"stop_atr":st,"trail_atr":99}
    for nm,costs in [("alpaca",C),("perp",bt.Costs(5,3))]:
        a=bt.run(f.loc[IS],pull2(rn,thr,ex)(f.loc[IS]),p,costs=costs).stats; b=bt.run(f.loc[OOS],pull2(rn,thr,ex)(f.loc[OOS]),p,costs=costs).stats
        rows.append(dict(costs=nm,cfg=(rn,thr,ex,mh,st),IS=a["sharpe"],OOS=b["sharpe"],win_oos=b.get("win_rate",0),n=b["trades"],dd=b["max_dd"]))
R=pd.DataFrame(rows)
for nm,g in R.groupby("costs"):
    top=g.sort_values("IS",ascending=False).head(3)
    print(f"  {nm}: OOS Sharpe>0 in {(g.OOS>0).mean():.0%} of {len(g)} configs; median OOS {g.OOS.median():.2f}; median OOS win {g.win_oos.median():.0%}")
    for _,r in top.iterrows(): print(f"     IS-best {r.cfg}: IS {r.IS:.2f} OOS {r.OOS:.2f} win {r.win_oos:.0%} n={r.n} dd={r.dd:.0%}")

print("=== C. Funding carry: long spot + short perp while trailing funding is positive")
h=raw.copy(); h=h.loc["2020-06-01":]
fund=data.load_funding("2020-01-01")
for look,thr in [(3,0.0),(3,0.00005),(7,0.00005),(14,0.0001)]:
    fd=fund.rolling(look*3).mean()                           # 3 fundings per day
    on=(fd>thr).reindex(h.index,method="ffill").fillna(False).shift(1).fillna(False)  # decide on known data, act next hour
    # hourly P&L per $1 of spot notional (perp short sized equal): spot ret - perp ret + funding at funding hours
    sr=h.spot_close.pct_change().fillna(0); pr=h.close.pct_change().fillna(0)
    fr=fund.reindex(h.index).fillna(0)
    pnl=on*(sr-pr+fr)
    trades=(on.astype(int).diff().fillna(0)!=0)
    cost=trades*(2*(0.0010+0.0003))   # two legs each switch (spot 10bp + perp 5bp + slippage)
    net=(pnl-cost)/1.25                # capital = spot + 25% perp margin
    eq=(1+net).cumprod()
    d=eq.resample("1D").last(); r=d.pct_change().dropna()
    yr=eq.resample("YE").last().pct_change(); yr.iloc[0]=eq.resample("YE").last().iloc[0]-1
    mo=eq.resample("ME").last().pct_change().dropna()
    dd=(eq/eq.cummax()-1).min()
    print(f"  funding avg {look}d > {thr*100:.3f}%: CAGR {eq.iloc[-1]**(365/((eq.index[-1]-eq.index[0]).days))-1:+.1%} Sharpe {r.mean()/r.std()*np.sqrt(365):.2f} maxDD {dd:.1%} "
          f"monthly win {(mo>0).mean():.0%} time-on {on.mean():.0%} | by year "+" ".join(f"{y.year}:{v:+.1%}" for y,v in yr.items()))

print("=== D. Profit-take variant scaled up to match v1.0 drawdown risk")
for tv in [0.3,0.4,0.5]:
    p={**P,"tp_atr":2,"tp_frac":0.33,"target_vol":tv}; line(f"  v1.0 + take 33% at +2 ATR, target_vol {tv}",evaluate(v1,p))
