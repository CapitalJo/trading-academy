"""Offline tests (no network):  python -m unittest discover -s tests"""
import os, sys, unittest
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from btc_bot import features, strategy, backtest as bt
from btc_bot.alpaca import PaperClient


def synthetic(n=4000, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=n, freq="4h", tz="UTC")
    flow = np.convolve(rng.normal(0, 0.05, n), np.ones(30) / 30, "same")
    ret = 0.002 * flow / 0.05 + rng.normal(0, 0.01, n)
    close = 30_000 * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]]
    vol = rng.uniform(800, 1200, n)
    df = pd.DataFrame(dict(open=open_, high=np.maximum(open_, close) * 1.003, low=np.minimum(open_, close) * 0.997,
                           close=close, volume=vol, quote_volume=vol * close, trades=1000.0,
                           taker_buy_base=vol * (0.5 + flow), spot_volume=vol, spot_taker_buy=vol * (0.5 + flow),
                           spot_close=close, funding=0.0001), index=idx)
    return df


class NoLookahead(unittest.TestCase):
    def test_signal_unchanged_by_future_data(self):
        df, p = synthetic(), dict(strategy.DEFAULTS, base_n=600)
        full_f = features.build(df, p); full = strategy.signal(full_f, p)
        for t in (1500, 2500, 3999):
            cut_f = features.build(df.iloc[:t + 1], p); cut = strategy.signal(cut_f, p)
            for k in full:
                self.assertEqual(full[k][t], cut[k][t], f"{k} at bar {t} depends on future data")
            np.testing.assert_allclose(full_f.iloc[t].to_numpy(dtype=float), cut_f.iloc[t].to_numpy(dtype=float), equal_nan=True)

    def test_backtest_trades_only_after_signal(self):
        df, p = synthetic(), dict(strategy.DEFAULTS, base_n=600)
        f = features.build(df, p); sig = strategy.signal(f, p)
        r = bt.run(f, sig, p)
        self.assertGreater(len(r.trades), 0)
        pos = {t: i for i, t in enumerate(f.index)}
        for t in r.trades.entry_time:
            self.assertTrue(sig["entry_long"][pos[t] - 1], "entry must follow a signal on the previous bar")


class Accounting(unittest.TestCase):
    def frame(self, opens, lows, closes):
        idx = pd.date_range("2024-01-01", periods=len(opens), freq="4h", tz="UTC")
        return pd.DataFrame(dict(open=opens, high=np.maximum(opens, closes), low=lows, close=closes,
                                 atr=10.0, rvol=1e-9), index=idx)

    def test_gap_through_stop_fills_at_open(self):
        f = self.frame([100, 100, 100, 80, 80], [100, 99, 99, 75, 79], [100, 100, 100, 80, 80])
        sig = {k: np.zeros(5, bool) for k in ("entry_long", "exit_long", "entry_short", "exit_short")}
        sig["entry_long"][0] = True
        p = dict(strategy.DEFAULTS, stop_atr=1.0, trail_atr=99, target_vol=1.0)   # stop at 90
        r = bt.run(f, sig, p, costs=bt.Costs(0, 0))
        tr = r.trades.iloc[0]
        self.assertEqual(tr.reason, "stop"); self.assertAlmostEqual(tr.exit, 80.0)   # gapped below 90 -> open

    def test_fees_round_trip(self):
        f = self.frame([100] * 5, [100] * 5, [100] * 5)
        sig = {k: np.zeros(5, bool) for k in ("entry_long", "exit_long", "entry_short", "exit_short")}
        sig["entry_long"][0] = True; sig["exit_long"][2] = True
        p = dict(strategy.DEFAULTS, target_vol=1.0)
        r = bt.run(f, sig, p, costs=bt.Costs(fee_bps=25, slippage_bps=0), start_equity=10_000)
        self.assertAlmostEqual(r.equity.iloc[-1], 10_000 - 25 - 25, delta=0.1)   # 0.25% in, 0.25% out


class Safety(unittest.TestCase):
    def test_live_url_refused(self):
        with self.assertRaises(SystemExit):
            PaperClient("k", "s", "https://api.alpaca.markets")
        PaperClient("k", "s", "https://paper-api.alpaca.markets/v2")   # ok


if __name__ == "__main__":
    unittest.main()
