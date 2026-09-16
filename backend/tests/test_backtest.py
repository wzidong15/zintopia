"""Deterministic checks for the backtest engine using synthetic closes (no network)."""

from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backtest as bt  # noqa: E402
from fastapi import HTTPException  # noqa: E402


def business_days(n: int, start: date = date(2020, 1, 1)) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def frame(cols: dict[str, np.ndarray]) -> pd.DataFrame:
    n = len(next(iter(cols.values())))
    return pd.DataFrame(cols, index=business_days(n))


class IndicatorTests(unittest.TestCase):
    def test_rsi_matches_paper_fund_rsi(self):
        rng = np.random.default_rng(1)
        x = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 120)))
        from portfolios import _rsi

        ours = bt.wilder_rsi(x, 14)
        self.assertAlmostEqual(ours[-1], _rsi(list(x), 14), places=9)
        self.assertTrue(np.isnan(ours[13]))
        self.assertFalse(np.isnan(ours[14]))

    def test_rsi_flat_is_50_and_up_only_is_100(self):
        self.assertEqual(bt.wilder_rsi(np.full(30, 10.0), 14)[-1], 50.0)
        self.assertEqual(bt.wilder_rsi(np.arange(1.0, 31.0), 14)[-1], 100.0)

    def test_cross_up_only_fires_on_the_crossing_bar(self):
        a = np.array([1.0, 1.0, 2.0, 3.0, 1.0, 2.0])
        b = np.array([1.5] * 6)
        self.assertEqual(bt._cross_up(a, b).tolist(), [False, False, True, False, False, True])


class SleeveTests(unittest.TestCase):
    def test_signal_at_prev_close_fills_next_close(self):
        close = np.array([100.0, 100.0, 110.0, 120.0, 120.0, 90.0])
        entry = np.array([False, True, False, False, False, False])
        exit_ = np.array([False, False, False, True, False, False])
        eq, trades, held = bt.simulate_sleeve(close, entry, exit_, cash0=1000, fees=0, slip=0)
        # entry signal at bar 1 -> buy at close[2]=110; exit signal at bar 3 -> sell at close[4]=120
        self.assertEqual(held.tolist(), [False, False, True, True, False, False])
        self.assertAlmostEqual(eq[2], 1000.0)
        self.assertAlmostEqual(eq[3], 1000.0 * 120 / 110)
        self.assertAlmostEqual(eq[4], 1000.0 * 120 / 110)
        self.assertAlmostEqual(eq[5], 1000.0 * 120 / 110)  # flat through the crash
        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(trades[0]["return"], 120 / 110 - 1)
        self.assertEqual(trades[0]["reason"], "signal")

    def test_costs_reduce_equity(self):
        close = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
        entry = np.array([True, True, True, True, True])
        exit_ = np.array([False, False, False, True, False])
        eq, _, _ = bt.simulate_sleeve(close, entry, exit_, cash0=1000, fees=0.001, slip=0.001)
        self.assertLess(eq[1], 1000.0)
        self.assertLess(eq[-1], eq[1])

    def test_stop_observed_on_close_fills_next_close(self):
        close = np.array([100.0, 100.0, 100.0, 90.0, 95.0, 96.0, 97.0])
        entry = np.ones(7, dtype=bool)
        exit_ = np.zeros(7, dtype=bool)
        eq, trades, _ = bt.simulate_sleeve(close, entry, exit_, cash0=1000, fees=0, slip=0, stop_loss=0.08)
        # buy at close[1]; close[3]=90 breaches 8% stop -> sold at close[4]=95
        self.assertEqual(trades[0]["exit"], 4)
        self.assertEqual(trades[0]["reason"], "stop")
        self.assertAlmostEqual(trades[0]["return"], 0.95 - 1)
        # with entry always true and no cross gate, it re-enters at close[5]
        self.assertGreater(eq[-1], eq[4])

    def test_stop_then_wait_for_cross(self):
        close = np.array([100.0, 100.0, 100.0, 90.0, 95.0, 96.0, 97.0, 98.0])
        entry = np.ones(8, dtype=bool)
        exit_ = np.zeros(8, dtype=bool)
        cross = np.zeros(8, dtype=bool)
        cross[6] = True
        eq, trades, _ = bt.simulate_sleeve(
            close, entry, exit_, cash0=1000, fees=0, slip=0, stop_loss=0.08, entry_after_stop=cross
        )
        self.assertEqual(trades[0]["exit"], 4)
        # no re-entry at 5 or 6; cross at 6 -> buy at close[7]
        self.assertAlmostEqual(eq[5], eq[4])
        self.assertAlmostEqual(eq[6], eq[4])

    def test_max_hold_exits(self):
        close = np.linspace(100, 110, 12)
        entry = np.ones(12, dtype=bool)
        exit_ = np.zeros(12, dtype=bool)
        _, trades, _ = bt.simulate_sleeve(close, entry, exit_, cash0=1000, fees=0, slip=0, max_hold=3)
        self.assertGreaterEqual(len(trades), 2)
        self.assertEqual(trades[0]["reason"], "max_hold")
        self.assertEqual(trades[0]["exit"] - trades[0]["entry"], 3)


class WeightTests(unittest.TestCase):
    def test_buy_hold_matches_benchmark_without_costs(self):
        rng = np.random.default_rng(2)
        a = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 200)))
        b = 50 * np.exp(np.cumsum(rng.normal(0, 0.01, 200)))
        closes = np.column_stack([a, b])
        eq, trades, exposure = bt.simulate_weights(closes, {0: np.array([0.5, 0.5])}, cash0=1000, fees=0, slip=0)
        bench = 1000 * np.mean(closes / closes[0], axis=1)
        np.testing.assert_allclose(eq, bench, rtol=1e-9)
        self.assertEqual(trades, [])
        self.assertAlmostEqual(float(exposure.mean()), 1.0)

    def test_rebalance_cannot_overspend(self):
        closes = np.array([[100.0, 100.0], [100.0, 100.0], [100.0, 100.0]])
        sched = {0: np.array([1.0, 0.0]), 1: np.array([0.0, 1.0]), 2: np.array([0.5, 0.5])}
        eq, trades, _ = bt.simulate_weights(closes, sched, cash0=1000, fees=0.01, slip=0.01)
        self.assertTrue(all(np.isfinite(eq)))
        self.assertLess(eq[-1], 1000.0)
        self.assertEqual(len(trades), 1)  # full exit of symbol 0 at bar 1
        self.assertEqual(trades[0]["reason"], "rebalance")


class CandidateTests(unittest.TestCase):
    def setUp(self):
        n = 700
        self.up = np.linspace(100, 200, n)
        self.down = np.linspace(200, 100, n)
        self.df = frame({"UP": self.up, "DOWN": self.down, "CASH": np.full(n, 100.0)})

    def test_momentum_rotation_tracks_the_riser(self):
        eq, trades, exposure = bt.run_candidate(
            self.df, 300, "momentum_rot", {"lookback": 126, "top_n": 1, "defensive": "CASH"},
            cash0=1000, fees=0, slip=0, trade_symbols=["UP", "DOWN", "CASH"],
        )
        self.assertAlmostEqual(eq[-1], 1000 * self.up[-1] / self.up[300], places=6)
        self.assertEqual(trades, [])  # never leaves UP
        self.assertAlmostEqual(float(exposure.mean()), 1.0)

    def test_momentum_rotation_falls_back_to_defensive(self):
        df = frame({"A": self.down, "B": self.down * 0.9, "CASH": np.full(700, 100.0)})
        eq, _, _ = bt.run_candidate(
            df, 300, "momentum_rot", {"lookback": 126, "top_n": 1, "defensive": "CASH"},
            cash0=1000, fees=0, slip=0, trade_symbols=["A", "B", "CASH"],
        )
        self.assertAlmostEqual(eq[-1], 1000.0)  # parked in flat CASH the whole time

    def test_trend_filter_sleeve_sidesteps_the_faller(self):
        eq, _, exposure = bt.run_candidate(
            self.df, 300, "trend_sma", {"window": 50, "stop_loss": 0},
            cash0=1000, fees=0, slip=0, trade_symbols=["DOWN"],
        )
        self.assertAlmostEqual(eq[-1], 1000.0)
        self.assertAlmostEqual(float(exposure.mean()), 0.0)

    def test_warmup_uses_history_before_start(self):
        # start at bar 300 with a 200-bar SMA: the ramp is above its SMA before the window,
        # so the signal from bar 299 fills at the first close of the window (bar 300)
        eq, _, exposure = bt.run_candidate(
            self.df, 300, "trend_sma", {"window": 200, "stop_loss": 0},
            cash0=1000, fees=0, slip=0, trade_symbols=["UP"],
        )
        self.assertAlmostEqual(float(exposure.mean()), 1.0)
        self.assertAlmostEqual(eq[-1], 1000 * self.up[-1] / self.up[300], places=6)

    def test_sma_cross_grid_skips_fast_ge_slow(self):
        spec = bt._SPEC_BY_ID["sma_cross"]
        combos = bt._expand_grid(spec, {"fast": [10, 50], "slow": [50, 100], "stop_loss": [0]}, 1)
        pairs = {(c["fast"], c["slow"]) for c in combos}
        self.assertEqual(pairs, {(10, 50), (10, 100), (50, 100)})

    def test_grid_validation(self):
        spec = bt._SPEC_BY_ID["rsi_reversion"]
        with self.assertRaises(HTTPException):
            bt._expand_grid(spec, {"entry": [80], "exit": [70]}, 1)
        spec = bt._SPEC_BY_ID["momentum_rot"]
        with self.assertRaises(HTTPException):
            bt._expand_grid(spec, {"top_n": [5]}, 2)

    def test_metrics_basic(self):
        dates = business_days(253)
        eq = np.linspace(1000, 1100, 253)
        m = bt.metrics(eq, dates, [{"entry": 0, "exit": 5, "return": 0.1, "pnl": 10}], 0.5, 1000)
        self.assertAlmostEqual(m["total_return"], 10.0)
        self.assertEqual(m["trades"], 1)
        self.assertEqual(m["win_rate"], 100.0)
        self.assertIsNone(m["profit_factor"])
        self.assertEqual(m["max_drawdown"], 0.0)


if __name__ == "__main__":
    unittest.main()


class WalkForwardTests(unittest.TestCase):
    def _runs(self, n: int):
        dates = business_days(n)
        # run A: strong early, flat late. run B: flat early, strong late. Both start at 1000.
        a = np.concatenate([np.linspace(1000, 2000, n // 2), np.full(n - n // 2, 2000.0)])
        b = np.concatenate([np.full(n // 2, 1000.0), np.linspace(1000, 2000, n - n // 2)])
        mk = lambda i, eq: {  # noqa: E731
            "id": i, "label": i, "strategy_label": "s", "_equity": eq, "_trades": [],
            "_exposure": np.ones(n), "cagr": 0.0, "sharpe": 0.0,
        }
        return dates, [mk("A", a), mk("B", b)]

    def test_fold_bounds_cover_the_rest_of_the_window(self):
        cfg = bt.WalkForward(folds=4, train_pct=50, mode="anchored")
        train0, folds = bt._fold_bounds(1000, cfg)
        self.assertEqual(train0, 500)
        self.assertEqual(folds[0][0], 500)
        self.assertEqual(folds[-1][1], 1000)
        self.assertEqual(sum(b - a for a, b in folds), 500)

    def test_too_short_window_is_rejected(self):
        with self.assertRaises(HTTPException):
            bt._fold_bounds(150, bt.WalkForward(folds=4, train_pct=50))

    def test_anchored_selection_uses_training_slice_only(self):
        dates, runs = self._runs(1000)
        bench = np.linspace(1000, 1500, 1000)
        wf = bt.walk_forward(runs, dates, bench, 1000.0, "total_return", bt.WalkForward(folds=2, train_pct=50, mode="anchored"))
        # fold 1 trains on bars 0-499 where A rose and B was flat -> picks A, which then goes flat OOS
        self.assertEqual(wf["segments"][0]["chosen_id"], "A")
        self.assertAlmostEqual(wf["segments"][0]["oos"]["total_return"], 0.0, places=6)
        # fold 2 trains on bars 0-749: A +100%, B +~50% -> still A under anchored total return
        self.assertEqual(wf["segments"][1]["chosen_id"], "A")
        self.assertEqual(wf["oos_start"], dates[500].isoformat())
        curve = [v for v in wf["oos_equity"] if v is not None]
        self.assertEqual(len(curve), 500)
        self.assertAlmostEqual(curve[0], 1000.0)
        self.assertAlmostEqual(wf["oos"]["total_return"], 0.0, places=6)
        self.assertFalse(wf["segments"][0]["beat_benchmark"])
        self.assertEqual(wf["folds_beating_benchmark"], 0)

    def test_rolling_selection_switches_to_recent_winner(self):
        dates, runs = self._runs(1000)
        bench = np.full(1000, 1000.0)
        wf = bt.walk_forward(runs, dates, bench, 1000.0, "total_return", bt.WalkForward(folds=2, train_pct=50, mode="rolling"))
        # fold 2 trains on bars 250-749 only: A +33% (500->? flat after 500), B +50% -> picks B, which keeps rising OOS
        self.assertEqual(wf["segments"][1]["chosen_id"], "B")
        self.assertGreater(wf["segments"][1]["oos"]["total_return"], 0)
        self.assertEqual(wf["distinct_selections"], 2)
        self.assertGreater(wf["oos"]["total_return"], 0)

    def test_stitched_curve_chains_fold_growth(self):
        dates, runs = self._runs(1000)
        bench = np.full(1000, 1000.0)
        wf = bt.walk_forward(runs, dates, bench, 1000.0, "total_return", bt.WalkForward(folds=2, train_pct=50, mode="rolling"))
        curve = [v for v in wf["oos_equity"] if v is not None]
        b = runs[1]["_equity"]
        expected_end = 1000.0 * (b[999] / b[750])  # fold 1 flat (A), fold 2 B's growth
        self.assertAlmostEqual(curve[-1], expected_end, places=1)
