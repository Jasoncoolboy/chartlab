"""Tests for the legacy backtest engine's cost timing (todo item 6).

Run from the repository root::

    python -m unittest tests.test_engine -v

Swap must roll over at 00:00 FTMO SERVER time whatever clock the bars are in,
one night per weekday that ends with the position open, x3 for Wednesday's
(the Wed->Thu rollover), none for Saturday and Sunday. The bars here follow
FTMO gold's session (01:05-23:50 server), so no bar ever sits on midnight.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

try:
    import numpy as np  # noqa: F401
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None

ROOT = Path(__file__).resolve().parents[1]

if pd is not None:
    sys.path.insert(0, str(ROOT / "tests"))
    from test_pipeline import _load_package
    _load_package()
    from chartlab import sources  # noqa: E402
    from chartlab.config import BacktestConfig, CostConfig  # noqa: E402
    from chartlab.engine import Strategy, Target, run_backtest  # noqa: E402


def gold_m1_server(first_day: str, days: int) -> pd.DatetimeIndex:
    """Server-time M1 labels of FTMO XAUUSD: Mon-Fri 01:05-23:49."""
    idx = pd.date_range(first_day, periods=days * 1440, freq="1min")
    mins = idx.hour * 60 + idx.minute
    return idx[(idx.weekday < 5) & (mins >= 65) & (mins < 23 * 60 + 50)]


def flat_m1(index: pd.DatetimeIndex) -> pd.DataFrame:
    """Constant prices, so nothing but costs moves the account."""
    n = len(index)
    cols = {f"bid_{k}": [2000.0] * n for k in ("open", "high", "low", "close")}
    cols.update({f"ask_{k}": [2000.3] * n for k in ("open", "high", "low", "close")})
    return pd.DataFrame(cols, index=index)


class Scripted(Strategy):
    """Holds ``direction`` while the last closed signal bar is in [open_after, close_after)."""
    signal_timeframe = "H1"

    def __init__(self, open_after, close_after, direction=1):
        self.lo, self.hi, self.direction = pd.Timestamp(open_after), pd.Timestamp(close_after), direction

    def decide(self, closed_bars, equity):
        t = closed_bars.index[-1]
        return Target(direction=self.direction if self.lo <= t < self.hi else 0, lots=1.0)


# Swap timing is tested with fixed numbers (priceData's XAUUSD swap on 2026-09-23, tester-verified
# -83 -> -249 over the Wednesday), so it does not move when priceData's cost table does.
TIMING_COSTS = CostConfig(contract_size_oz=100, commission_per_side_per_lot=0.0, commission_pct_side=0.0,
                          slippage_per_side_usd=0.0, swap_long_per_lot_per_day=-83.0,
                          swap_short_per_lot_per_day=-8.3) if pd is not None else None


def run(first_day, days, open_after, close_after, clock="ftmo", direction=1):
    """One scripted trade; the times are FTMO server labels in every clock."""
    server = gold_m1_server(first_day, days)
    lo, hi = pd.Timestamp(open_after), pd.Timestamp(close_after)
    if clock == "utc":
        index = sources.ftmo_server_to_utc(server)
        lo, hi = sources.ftmo_server_to_utc(pd.DatetimeIndex([lo, hi]))
    else:
        index = server
    m1 = flat_m1(index)
    signal = m1.resample("1h").first().dropna()
    res = run_backtest(m1, signal, Scripted(lo, hi, direction), TIMING_COSTS,
                       BacktestConfig(data_clock=clock, default_lots=1.0))
    trades = res.trades[res.trades.exit_reason == "signal"]
    assert len(trades) == 1, res.trades
    return trades.iloc[0]


@unittest.skipIf(pd is None, "pandas/numpy not installed")
class TestSwapTiming(unittest.TestCase):
    LONG = -83.0          # TIMING_COSTS, USD per lot per night
    SHORT = -8.3

    def test_tue_to_fri_pays_tue_wed_x3_thu(self):
        # 2025-06-10 is a Tuesday (US DST: server = UTC+3)
        t = run("2025-06-09", 5, "2025-06-10 12:00", "2025-06-13 12:00")
        self.assertAlmostEqual(t.swap, self.LONG * (1 + 3 + 1))

    def test_wed_to_thu_is_three_nights(self):
        t = run("2025-06-09", 5, "2025-06-11 12:00", "2025-06-12 12:00")
        self.assertAlmostEqual(t.swap, self.LONG * 3)            # tester: XAUUSD -83 -> -249

    def test_tue_to_wed_is_one_night(self):
        t = run("2025-06-09", 5, "2025-06-10 12:00", "2025-06-11 12:00")
        self.assertAlmostEqual(t.swap, self.LONG)

    def test_weekend_pays_friday_night_only(self):
        t = run("2025-06-09", 10, "2025-06-13 12:00", "2025-06-16 12:00")
        self.assertAlmostEqual(t.swap, self.LONG)

    def test_same_day_trade_pays_nothing(self):
        t = run("2025-06-09", 5, "2025-06-10 03:00", "2025-06-10 20:00")
        self.assertEqual(t.swap, 0.0)

    def test_short_uses_the_short_rate(self):
        t = run("2025-06-09", 5, "2025-06-11 12:00", "2025-06-12 12:00", direction=-1)
        self.assertAlmostEqual(t.swap, self.SHORT * 3)

    def test_utc_bars_roll_at_server_midnight_in_summer_and_winter(self):
        for first, lo, hi in (("2025-06-09", "2025-06-10 12:00", "2025-06-13 12:00"),     # UTC+3
                              ("2025-01-13", "2025-01-14 12:00", "2025-01-17 12:00")):    # UTC+2
            a = run(first, 5, lo, hi, clock="ftmo")
            b = run(first, 5, lo, hi, clock="utc")
            self.assertAlmostEqual(a.swap, self.LONG * 5, msg=first)
            self.assertAlmostEqual(b.swap, a.swap, msg=first)

    def test_close_at_the_first_bar_after_rollover_still_pays(self):
        # decided on the 23:00 bar, filled at 01:05 the next day: it was held over midnight
        t = run("2025-06-09", 5, "2025-06-10 12:00", "2025-06-10 23:00")
        self.assertEqual(pd.Timestamp(t.exit_time), pd.Timestamp("2025-06-11 01:05"))
        self.assertAlmostEqual(t.swap, self.LONG)

    def test_open_at_the_first_bar_after_rollover_does_not_pay(self):
        t = run("2025-06-09", 5, "2025-06-10 23:00", "2025-06-11 12:00")
        self.assertEqual(pd.Timestamp(t.entry_time), pd.Timestamp("2025-06-11 01:05"))
        self.assertEqual(t.swap, 0.0)

    def test_unknown_data_clock_is_refused(self):
        m1 = flat_m1(gold_m1_server("2025-06-09", 2))
        with self.assertRaises(ValueError):
            run_backtest(m1, m1.resample("1h").first().dropna(), Scripted("2025", "2026"),
                         TIMING_COSTS, BacktestConfig(data_clock="EET"))


def _cost_models():
    """priceData's COST_MODELS, read straight from its package (not through ChartLab)."""
    import importlib.util
    init = Path(r"C:\personalCode\tradingProjects\priceData") / "price_data" / "__init__.py"
    mod = sys.modules.get("_test_price_data")
    if mod is None:
        spec = importlib.util.spec_from_file_location("_test_price_data", init)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_test_price_data"] = mod          # its dataclasses look their module up
        spec.loader.exec_module(mod)
    return mod.COST_MODELS


HAVE_PRICEDATA = (Path(r"C:\personalCode\tradingProjects\priceData") / "price_data" / "__init__.py").exists()


@unittest.skipIf(pd is None or not HAVE_PRICEDATA, "needs pandas and the priceData package")
class TestCostsFromPriceData(unittest.TestCase):
    """FTMO costs come from priceData's COST_MODELS only: no copied number in ChartLab."""

    def test_defaults_are_pricedata_xauusd(self):
        m = _cost_models()["XAUUSD"]
        c = CostConfig()
        self.assertEqual(c.symbol, "XAUUSD")
        self.assertEqual(c.contract_size_oz, m["contract"])
        self.assertEqual((c.commission_per_side_per_lot, c.commission_pct_side), (0.0, m["commission_pct_side"]))
        self.assertAlmostEqual(c.slippage_per_side_usd, m["slippage_pip_per_fill"] * m["pip"])
        self.assertAlmostEqual(c.swap_long_per_lot_per_day, m["swap_long_pts"] * m["point"] * m["contract"])
        self.assertAlmostEqual(c.swap_short_per_lot_per_day, m["swap_short_pts"] * m["point"] * m["contract"])

    def test_fx_symbol_gets_the_flat_commission_and_its_own_swap(self):
        m = _cost_models()["EURUSD"]
        c = CostConfig(symbol="EURUSD")
        self.assertEqual((c.contract_size_oz, c.commission_per_side_per_lot, c.commission_pct_side),
                         (m["contract"], m["commission_usd_lot_side"], 0.0))
        self.assertAlmostEqual(c.slippage_per_side_usd, m["slippage_pip_per_fill"] * m["pip"])
        self.assertAlmostEqual(c.swap_long_per_lot_per_day, m["swap_long_pts"] * m["point"] * m["contract"])

    def test_an_explicit_value_wins_and_unsupported_symbols_are_refused(self):
        self.assertEqual(CostConfig(slippage_per_side_usd=0.0).slippage_per_side_usd, 0.0)
        for sym in ("USDJPY", "BTCUSD"):                   # non-USD quote / percent-of-price swap
            with self.assertRaises(ValueError):
                CostConfig(symbol=sym)


@unittest.skipIf(pd is None or not HAVE_PRICEDATA, "needs pandas and the priceData package")
class TestCostFile(unittest.TestCase):
    def test_old_file_with_flat_commission_warns_about_the_added_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "old.json"
            old.write_text(json.dumps({"commission_per_side_per_lot": 3.0}))
            with self.assertWarnsRegex(UserWarning, "commission_pct_side"):
                c = CostConfig.from_json(old)
            self.assertEqual((c.commission_per_side_per_lot, c.commission_pct_side),
                             (3.0, _cost_models()["XAUUSD"]["commission_pct_side"]))
            new = Path(tmp) / "fx.json"
            new.write_text(json.dumps({"commission_per_side_per_lot": 2.5, "commission_pct_side": 0}))
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                self.assertEqual(CostConfig.from_json(new).commission_pct_side, 0)
                CostConfig().to_json(Path(tmp) / "rt.json")
                self.assertEqual(CostConfig.from_json(Path(tmp) / "rt.json"), CostConfig())


if __name__ == "__main__":
    unittest.main()
