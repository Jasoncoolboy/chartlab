"""Tests for the pandas-based layers: export, pricedata, setups.

Run from the repository root::

    python -m unittest discover -s tests -v

These need pandas + numpy (+ pyarrow for the parquet ones); each class skips
itself when a dependency is missing, so the stdlib-only generator tests in
``test_chart.py`` still run anywhere. The real-priceData class skips when the
priceData folder is not present.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np
    import pandas as pd
except ImportError:  # pragma: no cover
    np = pd = None

try:
    import pyarrow  # noqa: F401
    HAVE_PARQUET = True
except ImportError:  # pragma: no cover
    HAVE_PARQUET = False

ROOT = Path(__file__).resolve().parents[1]
REAL_PRICEDATA = Path(r"C:\personalCode\priceData")


def _load_package():
    """Register the repository root as the ``chartlab`` package (as run.py does)."""
    existing = sys.modules.get("chartlab")
    if existing is not None and Path(existing.__file__).resolve().parent == ROOT:
        return existing
    spec = importlib.util.spec_from_file_location(
        "chartlab", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["chartlab"] = module
    spec.loader.exec_module(module)
    return module


if pd is not None:
    _load_package()
    from chartlab import chart, export, pricedata, setups  # noqa: E402


def make_frame(n=300, start="2026-01-05", freq="15min", base=1.1, digits=5, seed=1,
               style="priceData"):
    """A synthetic FX-like frame. ``style``: priceData | lower | bid."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq=freq, name="Datetime")
    close = np.round(base + np.cumsum(rng.normal(0, 0.0006, n)), digits)
    open_ = np.r_[close[0], close[:-1]]
    high = np.round(np.maximum(open_, close) + np.abs(rng.normal(0, 0.0002, n)), digits)
    low = np.round(np.minimum(open_, close) - np.abs(rng.normal(0, 0.0002, n)), digits)
    vol = rng.integers(50, 500, n)
    if style == "priceData":
        return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close,
                             "Volume": vol, "SpreadPts": 3}, index=idx)
    names = {"lower": ("open", "high", "low", "close", "volume"),
             "bid": ("bid_open", "bid_high", "bid_low", "bid_close", "bid_volume")}[style]
    return pd.DataFrame(dict(zip(names, (open_, high, low, close, vol))), index=idx)


@unittest.skipIf(pd is None, "pandas/numpy not installed")
class TestExport(unittest.TestCase):
    def test_reads_all_three_column_styles(self):
        for style in ("priceData", "lower", "bid"):
            df = make_frame(20, style=style)
            cols = export.ohlc_columns(df)
            self.assertEqual(set(cols), {"open", "high", "low", "close", "volume"}, style)
            block = export.bars_block(df, include_volume=True)
            self.assertEqual(len(block["time"]), 20)
            self.assertEqual(len(block["volume"]), 20)

    def test_unknown_columns_raise_naming_what_was_found(self):
        with self.assertRaises(KeyError) as ctx:
            export.bars_block(pd.DataFrame({"a": [1.0]}, index=pd.date_range("2026-01-01", periods=1)))
        self.assertIn("OHLC", str(ctx.exception))

    def test_five_digit_prices_survive_exactly(self):
        """The old 4 dp rounding turned 1.14448 into 1.1445 (a whole pip)."""
        df = make_frame(200)
        block = export.bars_block(df)
        self.assertEqual(block["open"], df["Open"].tolist())
        self.assertEqual(block["high"], df["High"].tolist())
        self.assertEqual(block["low"], df["Low"].tolist())
        self.assertEqual(block["close"], df["Close"].tolist())
        self.assertTrue(any(round(v, 4) != v for v in block["close"]))  # the case is real

    def test_epochs_treat_naive_time_as_utc_label(self):
        df = make_frame(2, start="2026-01-05 00:00")
        self.assertEqual(export.bars_block(df)["time"][0],
                         int(pd.Timestamp("2026-01-05", tz="UTC").timestamp()))

    def test_to_bid_frame_renames_only(self):
        df = make_frame(10)
        b = export.to_bid_frame(df)
        self.assertEqual(list(b.columns), ["bid_open", "bid_high", "bid_low", "bid_close", "bid_volume"])
        self.assertEqual(b["bid_close"].tolist(), df["Close"].tolist())
        self.assertIs(export.to_bid_frame(b), b)

    def test_indicator_keeps_fx_precision(self):
        ind = export.indicator("X", [1.14585, None, float("nan")], "#fff")
        self.assertEqual(ind["values"], [1.14585, None, None])


@unittest.skipIf(pd is None, "pandas/numpy not installed")
class TestCheckFrame(unittest.TestCase):
    def test_clean_frame_passes(self):
        self.assertEqual(pricedata.check_frame(make_frame(50)), [])

    def test_unsorted_duplicate_nan_incoherent_and_tz_are_flagged(self):
        df = make_frame(50)
        self.assertTrue(any("sorted" in p for p in pricedata.check_frame(df.iloc[::-1])))
        dup = pd.concat([df, df.iloc[[3]]]).sort_index()
        self.assertTrue(any("duplicate" in p for p in pricedata.check_frame(dup)))
        nan = df.copy()
        nan.iloc[4, 1] = np.nan
        self.assertTrue(any("NaN" in p for p in pricedata.check_frame(nan)))
        bad = df.copy()
        bad.iloc[7, bad.columns.get_loc("High")] = bad["Low"].iloc[7] - 0.01
        self.assertTrue(any("high <" in p for p in pricedata.check_frame(bad)))
        tz = df.copy()
        tz.index = tz.index.tz_localize("UTC")
        self.assertTrue(any("timezone" in p for p in pricedata.check_frame(tz)))


@unittest.skipIf(pd is None or not HAVE_PARQUET, "pandas/pyarrow not installed")
class TestPriceDataAdapter(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        d = self.root / "data" / "clean" / "EURUSD"
        d.mkdir(parents=True)
        self.m15 = make_frame(400)
        self.h1 = make_frame(100, freq="1h", seed=2)
        self.m15.to_parquet(d / "EURUSD_M15.parquet")
        self.h1.to_parquet(d / "EURUSD_H1.parquet")

    def tearDown(self):
        self._tmp.cleanup()

    def test_bad_symbol_or_timeframe_raises(self):
        with self.assertRaises(ValueError):
            pricedata.parquet_path("XXXYYY", "M15", self.root)
        with self.assertRaises(ValueError):
            pricedata.parquet_path("EURUSD", "M7", self.root)

    def test_missing_file_names_the_path(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            pricedata.load_frame("GBPUSD", "M15", root=self.root)
        self.assertIn("GBPUSD_M15.parquet", str(ctx.exception))

    def test_load_window_and_max_bars(self):
        df = pricedata.load_frame("EURUSD", "M15", start="2026-01-06", end="2026-01-07", root=self.root)
        self.assertTrue((df.index >= pd.Timestamp("2026-01-06")).all())
        self.assertTrue((df.index < pd.Timestamp("2026-01-07")).all())
        self.assertEqual(len(pricedata.load_frame("EURUSD", "M15", max_bars=25, root=self.root)), 25)
        with self.assertRaises(ValueError):
            pricedata.load_frame("EURUSD", "M15", start="2030-01-01", root=self.root)

    def test_corrupt_file_is_refused_not_charted(self):
        bad = self.m15.iloc[::-1]
        bad.to_parquet(self.root / "data" / "clean" / "EURUSD" / "EURUSD_M15.parquet")
        with self.assertRaises(ValueError) as ctx:
            pricedata.load_frame("EURUSD", "M15", root=self.root)
        self.assertIn("integrity", str(ctx.exception))

    def test_build_spec_is_valid_labelled_and_lossless(self):
        s = pricedata.build_spec("eurusd", ["M15", "H1"], root=self.root, volume=True,
                                 indicators="EMA20,RSI14")
        self.assertEqual(chart.validate(s), [])
        self.assertEqual(s["symbol"], "EURUSD")
        self.assertEqual(s["defaultTimeframe"], "M15")
        self.assertEqual(s["precision"], 5)
        self.assertEqual((s["source"], s["exchange"], s["tz"]), ("ftmo", "FTMO", "MYT"))
        self.assertIn("BID", s["periodLabel"])
        # times are TRUE UTC: 2026-01-05 00:00 server (winter, UTC+2) is 2026-01-04 22:00Z
        self.assertEqual(s["timeframes"]["M15"]["time"][0],
                         int(pd.Timestamp("2026-01-04 22:00", tz="UTC").timestamp()))
        self.assertEqual(s["timeframes"]["M15"]["close"], self.m15["Close"].tolist())
        self.assertEqual(len(s["timeframes"]["H1"]["volume"]), 100)
        self.assertEqual([i["name"] for i in s["indicators"]["M15"]], ["EMA20", "RSI14"])

    def test_build_spec_validates_arguments(self):
        with self.assertRaises(ValueError):
            pricedata.build_spec("EURUSD", ["M15"], default_tf="H4", root=self.root)
        with self.assertRaises(ValueError):
            pricedata.build_spec("EURUSD", [], root=self.root)

    def test_page_size_guard(self):
        old = pricedata.MAX_BARS_PER_TF
        pricedata.MAX_BARS_PER_TF = 100
        try:
            with self.assertRaises(ValueError) as ctx:
                pricedata.build_spec("EURUSD", ["M15"], root=self.root)
            self.assertIn("max_bars", str(ctx.exception))
            self.assertEqual(len(pricedata.build_spec("EURUSD", ["M15"], root=self.root,
                                                      max_bars=50)["timeframes"]["M15"]["time"]), 50)
        finally:
            pricedata.MAX_BARS_PER_TF = old

    def test_compact_render_of_fx_spec_decodes_to_the_same_bars(self):
        s = pricedata.build_spec("EURUSD", ["M15"], root=self.root)
        enc = chart.encode_timeframes(s["timeframes"], s["precision"])["M15"]
        raw = __import__("base64").b64decode(enc["c"])
        vals = [x / enc["s"] for x in __import__("struct").unpack("<%di" % (len(raw) // 4), raw)]
        self.assertEqual(vals, self.m15["Close"].tolist())


@unittest.skipIf(pd is None or not (REAL_PRICEDATA / "data" / "clean").is_dir(),
                 "real priceData folder not present")
class TestRealPriceData(unittest.TestCase):
    """Reads the actual clean parquet; proves the adapter against the real files."""

    def test_eurusd_m15_matches_the_parquet_bar_for_bar(self):
        raw = pd.read_parquet(REAL_PRICEDATA / "data" / "clean" / "EURUSD" / "EURUSD_M15.parquet").tail(500)
        s = pricedata.build_spec("EURUSD", ["M15"], max_bars=500, root=REAL_PRICEDATA)
        b = s["timeframes"]["M15"]
        self.assertEqual(b["open"], raw["Open"].tolist())
        self.assertEqual(b["high"], raw["High"].tolist())
        self.assertEqual(b["low"], raw["Low"].tolist())
        self.assertEqual(b["close"], raw["Close"].tolist())
        self.assertEqual(s["precision"], 5)

    def test_digits_per_instrument(self):
        for sym, want in (("EURUSD", 5), ("USDJPY", 3), ("XAUUSD", 2)):
            s = pricedata.build_spec(sym, ["H1"], max_bars=2000, root=REAL_PRICEDATA)
            self.assertEqual(s["precision"], want, sym)

    def test_every_symbol_timeframe_loads_and_passes_the_integrity_check(self):
        for sym in pricedata.SYMBOLS:
            for tf in ("H4", "D1", "W1", "MN"):
                df = pricedata.load_frame(sym, tf, root=REAL_PRICEDATA)
                self.assertGreater(len(df), 10, f"{sym} {tf}")


def _setups(rows, tf, **kw):
    """setups_from_rows for tests whose rows are labelled in UTC (the frames' own labels)."""
    return setups.setups_from_rows(rows, tf, clock=kw.pop("clock", "utc"), **kw)


@unittest.skipIf(pd is None, "pandas/numpy not installed")
class TestSetups(unittest.TestCase):
    def setUp(self):
        self.df = make_frame(600)                      # M15
        self.h1 = make_frame(150, freq="1h", seed=3)
        self.d1 = make_frame(120, freq="1D", seed=4, start="2025-10-01")
        self.trigger = self.df.index[300]

    def _row(self, **kw):
        i = 300
        entry = float(self.df["Open"].iloc[i + 1])
        row = {"dir": "long", "arm": str(self.df.index[i]), "entry_time": str(self.df.index[i + 1]),
               "entry_price": entry, "stop": entry - 0.0008, "target": entry + 0.0016}
        row.update(kw)
        return row

    # ---- rows -> Setup
    def test_minimal_row(self):
        (s,) = _setups([{"dir": "buy", "entry_time": str(self.trigger),
                                         "entry_price": 1.1}], "M15", symbol="EURUSD")
        self.assertEqual((s.dir, s.tf, s.symbol, s.kind), ("long", "M15", "EURUSD", "external"))
        self.assertEqual(s.trigger_time, s.entry_time)     # no arm -> trigger is the entry
        self.assertIsNone(s.stop)
        self.assertEqual(s.zone, [])

    def test_aliases_and_zone_list(self):
        z = {"start": str(self.df.index[280]), "end": str(self.trigger), "low": 1.0, "high": 1.2}
        (s,) = _setups([{"side": -1, "fill": str(self.trigger), "entry": 1.1,
                                         "sl": 1.101, "tp": 1.098, "zone": [z], "ref": "Z 1/a",
                                         "lots": 0.1}], "M15")
        self.assertEqual((s.dir, s.stop, s.target, s.lots), ("short", 1.101, 1.098, 0.1))
        self.assertEqual(s.id, "Z_1_a")                    # safe as a filename
        self.assertEqual(s.zone[0]["low"], 1.0)

    def test_bad_rows_are_refused_with_the_row_number(self):
        base = {"dir": "long", "entry_time": str(self.trigger), "entry_price": 1.1}
        cases = [
            ({"dir": "up"}, "row 0.*dir"),
            ({"entry_price": None}, "row 0.*entry_price"),
            ({"entry_price": "abc"}, "not a number"),
            ({"entry_price": float("nan")}, "not finite"),
            ({"stop": 1.2}, "long stop"),
            ({"target": 1.0}, "long target"),
        ]
        for patch, pattern in cases:
            with self.assertRaisesRegex(ValueError, pattern):
                _setups([{**base, **patch}], "M15")
        with self.assertRaisesRegex(ValueError, "entry_time"):
            _setups([{"dir": "long", "entry_price": 1.1}], "M15")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            _setups([{**base, "id": "a"}, {**base, "id": "a"}], "M15")

    def test_short_side_sanity(self):
        with self.assertRaisesRegex(ValueError, "short stop"):
            _setups([{"dir": "short", "entry_time": str(self.trigger),
                                      "entry_price": 1.1, "stop": 1.09}], "M15")

    # ---- Setup -> page
    def test_page_uses_setup_symbol_not_a_hardcoded_one(self):
        (s,) = _setups([self._row()], "M15", symbol="GBPUSD")
        page = setups.slice_spec(self.df, s, 30, 20)
        self.assertEqual(page["symbol"], "GBPUSD")
        self.assertEqual(page["precision"], 5)
        self.assertNotIn("lots", page["overlay"]["trades"][0])   # no invented 0.5 lots
        (t,) = _setups([self._row(lots=0.25, symbol="USDJPY")], "M15")
        self.assertEqual(setups.slice_spec(self.df, t)["symbol"], "USDJPY")
        self.assertEqual(setups.slice_spec(self.df, t)["overlay"]["trades"][0]["lots"], 0.25)

    def test_window_and_trade_and_exit(self):
        (s,) = _setups([self._row(exit_time=str(self.df.index[320]),
                                                  exit_price=1.1, net=12.5, reason="tp")], "M15")
        page = setups.slice_spec(self.df, s, 30, 20, tz="UTC", source="ftmo")
        b = page["timeframes"]["M15"]
        self.assertEqual(len(b["time"]), 30 + 20 + 1)
        tr = page["overlay"]["trades"][0]
        self.assertEqual((tr["net"], tr["reason"]), (12.5, "tp"))
        self.assertEqual((page["tz"], page["source"]), ("UTC", "ftmo"))
        self.assertEqual(chart.validate(page), [])

    def test_trigger_outside_the_bars_is_refused_not_clamped(self):
        far = self._row(arm="2031-01-01 00:00", entry_time="2031-01-01 00:15")
        early = self._row(arm="2020-01-01 00:00", entry_time="2020-01-01 00:15")
        for row in (far, early):
            (s,) = _setups([row], "M15")
            with self.assertRaisesRegex(ValueError, "outside"):
                setups.slice_spec(self.df, s)

    def test_extra_timeframes_cover_window_left_widened_right_not_extended(self):
        (s,) = _setups([self._row()], "M15")
        page = setups.slice_spec(self.df, s, 30, 20, extra_frames={"H1": self.h1, "D1": self.d1})
        self.assertEqual(list(page["timeframes"]), ["M15", "H1", "D1"])
        m15 = page["timeframes"]["M15"]["time"]
        end = m15[-1] + 900
        for tf in ("H1", "D1"):
            t = page["timeframes"][tf]["time"]
            self.assertLessEqual(t[0], m15[0], tf)          # contains the window start
            self.assertLess(t[-1], end, tf)                 # nothing opens past the window's end
            self.assertGreaterEqual(len(t), min(setups.MIN_EXTRA_BARS, len(t)), tf)
        self.assertGreaterEqual(len(page["timeframes"]["D1"]["time"]), 1)

    def test_oversized_extra_view_is_skipped(self):
        (s,) = _setups([self._row()], "M15")
        old = setups.MAX_EXTRA_BARS
        setups.MAX_EXTRA_BARS = 10
        try:
            page = setups.slice_spec(self.df, s, 30, 20, extra_frames={"H1": self.h1})
        finally:
            setups.MAX_EXTRA_BARS = old
        self.assertEqual(list(page["timeframes"]), ["M15"])

    def test_render_pages_and_catalog_escape_and_dedupe(self):
        rows = [self._row(id="one", label="<b>x</b> & co"), self._row(id="two")]
        found = _setups(rows, "M15", symbol="EURUSD")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "pages"
            pages = setups.render_pages(self.df, found, out, lib_dir="lib", symbol="EURUSD", tz="MYT")
            self.assertEqual(sorted(p.name for p in pages), ["one.html", "two.html"])
            index = setups.write_catalog(out, found, {"label": "L<i>", "tf": "M15", "tz": "MYT"})
            html = index.read_text(encoding="utf-8")
            self.assertNotIn("<b>x</b>", html)
            self.assertIn("&lt;b&gt;x&lt;/b&gt; &amp; co", html)
            self.assertIn("times in MYT", html)
            self.assertNotIn(" UTC", html)
            self.assertIn(f"<td>{found[0].entry_price:.5f}</td>", html)   # 5-digit FX shown in full
            self.assertIn("● Long", html)               # real glyph, not an escape sequence
            data = json.loads((out / "_setups.json").read_text(encoding="utf-8"))
            self.assertEqual(len(data["rows"]), 2)
            with self.assertRaisesRegex(ValueError, "duplicate"):
                setups.render_pages(self.df, found + found[:1], Path(tmp) / "dup", lib_dir="lib")

    # ---- built-in finders accept priceData-shaped frames
    def test_builtin_finder_same_result_for_priceData_and_bid_frames(self):
        opts = {"limit": None, "start": None, "end": None, "dir": None}
        a = setups.find_all(make_frame(400, style="priceData"), "donchian", {"n": 10, "sl_atr": 1.5, "tp_atr": 3}, opts)
        b = setups.find_all(make_frame(400, style="bid"), "donchian", {"n": 10, "sl_atr": 1.5, "tp_atr": 3}, opts)
        self.assertGreater(len(a), 0)
        self.assertEqual([(s.id, s.entry_price, s.stop, s.target) for s in a],
                         [(s.id, s.entry_price, s.stop, s.target) for s in b])

    def test_builtin_setup_entry_is_the_next_bars_open(self):
        """No look-ahead in the finder: signal on bar i's close, fill at bar i+1's open."""
        df = make_frame(400)
        found = setups.find_all(df, "donchian", {"n": 10, "sl_atr": 1.5, "tp_atr": 3},
                                {"limit": None, "start": None, "end": None, "dir": None})
        opens = dict(zip((int(t.timestamp()) for t in df.index), df["Open"]))
        for s in found:
            self.assertGreater(s.entry_time, s.trigger_time)
            self.assertEqual(s.entry_price, opens[s.entry_time])   # exact, not pip-rounded


if __name__ == "__main__":
    unittest.main()
