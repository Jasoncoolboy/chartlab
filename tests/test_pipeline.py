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
    from chartlab import chart, export, pricedata, setups, sources  # noqa: E402


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

    def test_build_spec_view_needs_a_clock_and_is_converted(self):
        with self.assertRaises(sources.ClockError):
            pricedata.build_spec("EURUSD", ["M15"], root=self.root, view=("2026-01-06 10:00", "2026-01-06 14:00"))
        s = pricedata.build_spec("EURUSD", ["M15"], root=self.root, clock="ftmo",
                                 view={"from": "2026-01-06 10:00", "to": "2026-01-06 14:00"})
        utc = lambda t: int(pd.Timestamp(t, tz="UTC").timestamp())
        self.assertEqual(s["view"], {"from": utc("2026-01-06 08:00"), "to": utc("2026-01-06 12:00")})

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


def write_manifest(root, symbol="EURUSD", *, clean_from=None, price_clean_from=None, years=None,
                   verified=None, findings=None):
    """A priceData ``data/clean/manifest.json`` with only the fields ChartLab reads.

    ``verified`` = {tf: True | False}: priceData's own HTF-vs-M1 verdict per timeframe.
    """
    tfs = {tf: {"htf_verified": v, "htf_findings": (findings or {}).get(tf, {}),
                "verified_at": "2026-09-26T13:29:01", "dirty_reason": None}
           for tf, v in (verified or {}).items()}
    info = {"symbol": symbol, "clean_from": clean_from, "price_clean_from": price_clean_from,
            "quality_by_year": years or {}, "timeframes": tfs}
    path = Path(root) / "data" / "clean" / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"symbols": {symbol: info}}), encoding="utf-8")


def make_m1_server(start="2026-03-04", days=9, seed=5):
    """FX-like M1 in FTMO SERVER time: Mon-Fri 00:05-23:55, across the US DST switch
    (2026-03-08) so the UTC conversion changes offset inside the data."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=days * 1440, freq="1min", name="Datetime")
    idx = idx[(idx.weekday < 5) & (idx.hour * 60 + idx.minute >= 5) & (idx.hour * 60 + idx.minute <= 23 * 60 + 55)]
    n = len(idx)
    close = np.round(1.1 + np.cumsum(rng.normal(0, 0.0001, n)), 5)
    open_ = np.r_[close[0], close[:-1]]
    high = np.round(np.maximum(open_, close) + np.abs(rng.normal(0, 0.00005, n)), 5)
    low = np.round(np.minimum(open_, close) - np.abs(rng.normal(0, 0.00005, n)), 5)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close,
                         "Volume": rng.integers(1, 50, n), "SpreadPts": 2}, index=idx)


@unittest.skipIf(pd is None or not HAVE_PARQUET, "pandas/pyarrow not installed")
class TestBuiltFromM1(unittest.TestCase):
    """todo item 3: native higher-timeframe holes must not pass silently into pages."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dir = self.root / "data" / "clean" / "EURUSD"
        self.dir.mkdir(parents=True)
        self.m1 = make_m1_server()
        self.m1.to_parquet(self.dir / "EURUSD_M1.parquet")
        self.native = {}
        for tf in ("M15", "H1", "H4", "D1"):          # the "broker's" files: exact M1 aggregates
            nat = pricedata.aggregate_m1(self.m1, tf)
            nat["SpreadPts"] = 2
            nat.attrs = {}
            nat.to_parquet(self.dir / f"EURUSD_{tf}.parquet")
            self.native[tf] = nat
        write_manifest(self.root, verified=dict.fromkeys(self.native, True))   # as priceData records it

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, tf, frame):
        frame.to_parquet(self.dir / f"EURUSD_{tf}.parquet")

    def test_bins_sit_on_the_server_grid(self):
        h4 = pricedata.aggregate_m1(self.m1, "H4")
        self.assertTrue((h4.index.hour % 4 == 0).all() and (h4.index.minute == 0).all())
        d1 = pricedata.aggregate_m1(self.m1, "D1")
        self.assertTrue((d1.index.hour == 0).all())
        self.assertEqual(set(pricedata.aggregate_m1(self.m1, "W1").index.weekday), {6})     # Sunday
        self.assertTrue((pricedata.aggregate_m1(self.m1, "MN").index.day == 1).all())
        first = self.m1.loc[self.m1.index < h4.index[0] + pd.Timedelta(hours=4)]
        self.assertEqual((h4["Open"].iloc[0], h4["High"].iloc[0], h4["Low"].iloc[0], h4["Close"].iloc[0]),
                         (first["Open"].iloc[0], first["High"].max(), first["Low"].min(), first["Close"].iloc[-1]))
        with self.assertRaises(ValueError):
            pricedata.aggregate_m1(self.m1, "M7")

    def test_m1_build_equals_the_native_file_in_utc(self):
        for tf in ("M15", "H1", "H4", "D1"):
            nat = pricedata.load_frame("EURUSD", tf, root=self.root)
            built = pricedata.load_frame("EURUSD", tf, root=self.root, bars="m1")
            self.assertTrue(nat.index.equals(built.index), tf)
            for c in ("Open", "High", "Low", "Close"):
                self.assertEqual(nat[c].tolist(), built[c].tolist(), f"{tf} {c}")
        # a window and max_bars behave as for native frames (UTC bounds)
        w = pricedata.load_frame("EURUSD", "H1", root=self.root, bars="m1",
                                 start="2026-03-09 03:00", end="2026-03-10 00:00")
        n = pricedata.load_frame("EURUSD", "H1", root=self.root, start="2026-03-09 03:00", end="2026-03-10 00:00")
        self.assertTrue(w.index.equals(n.index))
        self.assertEqual(w["High"].tolist(), n["High"].tolist())
        self.assertEqual(len(pricedata.load_frame("EURUSD", "H4", root=self.root, bars="m1", max_bars=7)), 7)
        with self.assertRaises(ValueError):
            pricedata.load_frame("EURUSD", "H1", root=self.root, bars="tick")

    def test_clean_native_frames_check_clean(self):
        frames = {tf: pricedata.load_frame("EURUSD", tf, root=self.root) for tf in self.native}
        res = pricedata.check_vs_m1("EURUSD", frames, root=self.root)
        self.assertEqual({tf: r["status"] for tf, r in res.items()}, dict.fromkeys(frames, "clean"))
        self.assertTrue(all(r["compared"] > 0 for r in res.values()))
        self.assertEqual(pricedata.describe_check("EURUSD", res), [])

    def test_holes_wrong_bars_and_orphans_are_counted_exactly(self):
        bad = self.native["H1"].copy()
        gone = bad.index[[30, 31, 32]]
        bad = bad.drop(gone)
        changed = bad.index[50]
        bad.loc[changed, "High"] += 0.0005
        orphan = pd.Timestamp("2026-03-07 12:00")                       # a Saturday: no M1 behind it
        bad.loc[orphan] = [1.1, 1.1, 1.1, 1.1, 1, 2]
        self._write("H1", bad.sort_index())
        frame = pricedata.load_frame("EURUSD", "H1", root=self.root)
        r = pricedata.check_vs_m1("EURUSD", {"H1": frame}, root=self.root)["H1"]
        conv = lambda t: sources.ftmo_server_to_utc(pd.DatetimeIndex(t))
        self.assertEqual(r["status"], "dirty")
        self.assertTrue(r["missing"].equals(conv(gone)))
        self.assertEqual(list(r["mismatched"]), list(conv([changed])))
        self.assertEqual(list(r["extra"]), list(conv([orphan])))
        (line,) = pricedata.describe_check("EURUSD", {"H1": r})
        self.assertIn("3 missing", line)
        self.assertIn("1 mismatched", line)
        # a window that starts inside the file's history still sees a hole at its very start
        w = pricedata.check_vs_m1("EURUSD", {"H1": frame}, start=r["missing"][0], root=self.root)["H1"]
        self.assertEqual(len(w["missing"]), 3)

    def test_build_spec_warns_and_m1_bars_fix_it(self):
        bad = self.native["H4"].drop(self.native["H4"].index[[5, 6]])
        self._write("H4", bad)
        with self.assertWarnsRegex(pricedata.DataWarning, r"EURUSD H4: .*2 missing.*bars='m1'"):
            s = pricedata.build_spec("EURUSD", ["H1", "H4"], root=self.root)
        self.assertEqual(len(s["timeframes"]["H4"]["time"]), len(self.native["H4"]) - 2)
        import warnings as _w
        with _w.catch_warnings():
            _w.simplefilter("error", pricedata.DataWarning)
            s = pricedata.build_spec("EURUSD", ["H1", "H4"], root=self.root, bars="m1")
            self.assertEqual(len(s["timeframes"]["H4"]["time"]), len(self.native["H4"]))
            pricedata.build_spec("EURUSD", ["H1", "H4"], root=self.root, verify_m1=False)

    def test_no_m1_file_is_unverified_never_clean(self):
        (self.dir / "EURUSD_M1.parquet").unlink()
        frames = {"H1": pricedata.load_frame("EURUSD", "H1", root=self.root)}
        r = pricedata.check_vs_m1("EURUSD", frames, root=self.root)["H1"]
        self.assertEqual(r["status"], "unverified")
        with self.assertWarnsRegex(pricedata.DataWarning, "NOT verified"):
            pricedata.build_spec("EURUSD", ["H1"], root=self.root)


@unittest.skipIf(pd is None or not HAVE_PARQUET, "pandas/pyarrow not installed")
class TestCleanYearsOnly(unittest.TestCase):
    """priceData's clean years only by default; pre-clean years only on request, only where their prices are
    verified, and labelled. The fake priceData splits at 2026-01-01 like the real one splits at 2022-01-01:
    2025-12-29 is a repaired (not price-clean) day, 2025-12-30/31 are price-clean pre-clean days."""

    CUT = pd.Timestamp("2026-01-01") if pd is not None else None
    TFS = ("M15", "H1", "H4", "D1")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.m1 = make_m1_server(start="2025-12-29", days=12)
        frames = {"M1": self.m1, **{tf: pricedata.aggregate_m1(self.m1, tf) for tf in self.TFS}}
        for tf, f in frames.items():
            f = f.copy()
            f["SpreadPts"] = 2
            f.attrs = {}
            for part, keep in (("clean", f.index >= self.CUT), ("pre_clean", f.index < self.CUT)):
                d = self.root / "data" / part / "EURUSD"
                d.mkdir(parents=True, exist_ok=True)
                f.loc[keep].to_parquet(d / f"EURUSD_{tf}.parquet")
        self.full = frames
        self.manifest(years={"2025": {"price_clean": True}})

    def tearDown(self):
        self._tmp.cleanup()

    def manifest(self, years, verified=None, findings=None):
        write_manifest(self.root, clean_from="2026-01-01", price_clean_from="2025-12-30", years=years,
                       verified=verified or dict.fromkeys(self.TFS, True), findings=findings)

    def load(self, tf, **kw):
        import warnings as _w
        with _w.catch_warnings(record=True) as caught:
            _w.simplefilter("always", pricedata.DataWarning)
            df = pricedata.load_frame("EURUSD", tf, root=self.root, **kw)
        return df, [str(w.message) for w in caught if issubclass(w.category, pricedata.DataWarning)]

    def test_clean_from_and_pre_clean_from_come_from_the_manifest(self):
        self.assertEqual(pricedata.clean_from("EURUSD", self.root), self.CUT)
        self.assertEqual(pricedata.pre_clean_from("EURUSD", self.root), pd.Timestamp("2025-12-30"))
        self.manifest(years={"2025": {"price_clean": False}})         # a year whose prices are not verified
        self.assertIsNone(pricedata.pre_clean_from("EURUSD", self.root))
        (self.root / "data" / "clean" / "manifest.json").unlink()
        self.assertIsNone(pricedata.clean_from("EURUSD", self.root))
        self.assertIsNone(pricedata.pre_clean_from("EURUSD", self.root))

    def test_default_is_clean_years_only_and_an_early_start_says_so(self):
        cut_utc = pricedata.server_to_utc(self.CUT)
        df, warned = self.load("H1")
        self.assertGreaterEqual(df.index[0], cut_utc)
        self.assertEqual(warned, [])
        self.assertNotIn("pre_clean_rows", df.attrs)
        df, warned = self.load("H1", start="2025-12-29")
        self.assertGreaterEqual(df.index[0], cut_utc)
        (msg,) = warned
        self.assertIn("starts 2026-01-01", msg)
        self.assertIn("pre_clean=True", msg)
        self.assertIn("2025-12-30", msg)

    def test_pre_clean_adds_only_the_price_verified_days_and_tags_them(self):
        pcf = pricedata.server_to_utc(pd.Timestamp("2025-12-30"))
        cut_utc = pricedata.server_to_utc(self.CUT)
        for bars in ("native", "m1"):
            df, warned = self.load("H1", pre_clean=True, bars=bars)
            self.assertEqual(warned, [], bars)
            self.assertGreaterEqual(df.index[0], pcf, bars)                   # never the repaired 2025-12-29
            self.assertLess(df.index[0], cut_utc, bars)
            self.assertEqual(df.attrs["pre_clean_rows"], int((df.index < cut_utc).sum()), bars)
            self.assertEqual(df.attrs["pre_clean_until"], cut_utc, bars)
            want = sources.ftmo_server_to_utc(self.full["H1"].index[self.full["H1"].index >= "2025-12-30"])
            self.assertTrue(df.index.equals(pd.DatetimeIndex(want)), bars)
        df, warned = self.load("H1", pre_clean=True, start="2025-12-29")
        self.assertIn("nothing before 2025-12-30", warned[0])
        df, _ = self.load("H1", pre_clean=True, start="2026-01-05")         # a window after the cut
        self.assertNotIn("pre_clean_rows", df.attrs)
        self.manifest(years={"2025": {"price_clean": False}})
        df, warned = self.load("H1", pre_clean=True, start="2025-12-29")
        self.assertGreaterEqual(df.index[0], cut_utc)
        self.assertIn("no price-verified years", warned[0])

    def test_pre_clean_bars_are_checked_against_pre_clean_m1(self):
        frames = {tf: self.load(tf, pre_clean=True)[0] for tf in self.TFS}
        res = pricedata.check_vs_m1("EURUSD", frames, root=self.root)
        self.assertEqual({tf: r["status"] for tf, r in res.items()}, dict.fromkeys(self.TFS, "clean"))
        cut_utc = pricedata.server_to_utc(self.CUT)
        holed = frames["H1"].drop(frames["H1"].index[[3, 4]])                 # holes inside the pre-clean days
        self.assertLess(holed.index[5], cut_utc)
        r = pricedata.check_vs_m1("EURUSD", {"H1": holed}, root=self.root)["H1"]
        self.assertEqual(list(r["missing"]), list(frames["H1"].index[[3, 4]]))

    def test_page_notes_say_what_the_bars_are(self):
        import warnings as _w
        with _w.catch_warnings():
            _w.simplefilter("error", pricedata.DataWarning)
            clean = pricedata.build_spec("EURUSD", ["H1", "H4"], root=self.root)
            pre = pricedata.build_spec("EURUSD", ["H1", "H4"], root=self.root, pre_clean=True)
            m1 = pricedata.build_spec("EURUSD", ["H1", "H4"], root=self.root, bars="m1")
        self.assertEqual([n["level"] for n in clean["notes"]], ["info"])
        self.assertIn("priceData verified these native bars against M1 (2026-09-26 13:29)", clean["notes"][0]["text"])
        self.assertIn("ChartLab re-checked", clean["notes"][0]["text"])
        self.assertEqual([n["level"] for n in pre["notes"]], ["warn", "info"])
        self.assertIn("PRE-CLEAN", pre["notes"][0]["text"])
        self.assertIn(f"{pricedata.server_to_utc(self.CUT):%Y-%m-%d %H:%M} UTC", pre["notes"][0]["text"])
        self.assertEqual(m1["notes"], [{"level": "info", "text": "EURUSD H1, H4: every timeframe built from priceData M1"}])
        self.assertEqual(chart.validate(pre), [])

    def test_a_failed_pricedata_verdict_warns_and_is_on_the_page(self):
        self.manifest(years={"2025": {"price_clean": True}}, verified={"H1": True, "H4": False},
                      findings={"H4": {"missing": 2, "mismatch": 0}})
        with self.assertWarnsRegex(pricedata.DataWarning, r"EURUSD H4: priceData's HTF-vs-M1 verification FAILED \(2 missing\)"):
            s = pricedata.build_spec("EURUSD", ["H1", "H4"], root=self.root)
        self.assertEqual([n["level"] for n in s["notes"]], ["warn"])
        self.assertIn("FAILED", s["notes"][0]["text"])

    def test_cli_rows_label_only_the_pages_that_show_pre_clean_bars(self):
        import re
        from chartlab import cli
        rows = [{"dir": "long", "entry_time": "2025-12-31 10:00", "entry_price": 1.1, "id": "old"},
                {"dir": "long", "entry_time": "2026-01-07 10:00", "entry_price": 1.1, "id": "new"}]
        with tempfile.TemporaryDirectory() as tmp:
            rows_file = Path(tmp) / "rows.json"
            rows_file.write_text(json.dumps(rows))
            out = Path(tmp) / "pages"
            args = ["rows", "--rows", str(rows_file), "--root", str(self.root), "--symbol", "EURUSD",
                    "--timeframe", "H1", "--clock", "ftmo", "--pre", "5", "--post", "5", "--out", str(out)]
            with self.assertRaises(ValueError):                       # clean years only: 12-31 is not there
                cli.main(args)
            cli.main(args + ["--pre-clean"])
            notes = {}
            for sid in ("old", "new"):
                html = (out / f"{sid}.html").read_text(encoding="utf-8")
                payload = re.search(r'<script id="payload" type="application/json">(.*?)</script>', html, re.S)
                notes[sid] = json.loads(payload.group(1))["notes"]
        self.assertEqual([n["level"] for n in notes["old"]], ["warn", "info"])
        self.assertIn("PRE-CLEAN", notes["old"][0]["text"])
        self.assertEqual([n["level"] for n in notes["new"]], ["info"])
        self.assertIn("ChartLab re-checked this page's H1 bars against M1: they match", notes["new"][0]["text"])

    def test_short_windows_unverifiable_by_m1_are_covered_by_the_pricedata_verdict(self):
        res = {"W1": {"status": "unverified", "reason": "M1 covers no whole bar in the window", "short_window": True},
               "MN": {"status": "unverified", "reason": "no M1 file at x"},
               "H1": {"status": "clean"}}
        ok = {"W1": {"verified": True}, "MN": {"verified": True}}
        self.assertEqual(list(pricedata.covered_by_pricedata(res, ok)), ["MN", "H1"])     # no M1 file stays
        self.assertEqual(list(pricedata.covered_by_pricedata(res, {"W1": {"verified": None}})), ["W1", "MN", "H1"])

    def test_notes_never_claim_a_check_that_did_not_run(self):
        (self.root / "data" / "clean" / "EURUSD" / "EURUSD_M1.parquet").unlink()
        with self.assertWarnsRegex(pricedata.DataWarning, "NOT verified against M1"):
            s = pricedata.build_spec("EURUSD", ["H1"], root=self.root)
        self.assertEqual([n["level"] for n in s["notes"]], ["warn"])
        self.assertNotIn("re-checked", s["notes"][0]["text"])


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

    def test_native_files_match_m1_on_a_clean_year(self):
        """2025 is clean in priceData (its own verify_htf_vs_m1 agrees): every timeframe must match."""
        for sym in ("EURUSD", "USDJPY", "XAUUSD", "BTCUSD"):
            frames = {tf: pricedata.load_frame(sym, tf, root=REAL_PRICEDATA)
                      for tf in ("M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN")}
            res = pricedata.check_vs_m1(sym, frames, start="2025-01-01", end="2026-01-01", root=REAL_PRICEDATA)
            for tf, r in res.items():
                self.assertEqual((r["status"], len(r["missing"]), len(r["mismatched"]), len(r["extra"])),
                                 ("clean", 0, 0, 0), f"{sym} {tf}")
                self.assertGreater(r["compared"], 10, f"{sym} {tf}")

    def test_real_holes_are_found_when_made(self):
        h1 = pricedata.load_frame("EURUSD", "H1", root=REAL_PRICEDATA, start="2025-03-01", end="2025-04-01")
        holed = h1.drop(h1.index[[40, 41, 42, 43, 44]])
        r = pricedata.check_vs_m1("EURUSD", {"H1": holed}, start="2025-03-01", end="2025-04-01",
                                  root=REAL_PRICEDATA)["H1"]
        self.assertEqual(list(r["missing"]), list(h1.index[[40, 41, 42, 43, 44]]))
        self.assertEqual((len(r["mismatched"]), len(r["extra"])), (0, 0))

    def test_every_symbol_timeframe_loads_and_passes_the_integrity_check(self):
        for sym in pricedata.SYMBOLS:
            for tf in ("H4", "D1", "W1", "MN"):
                df = pricedata.load_frame(sym, tf, root=REAL_PRICEDATA)
                self.assertGreater(len(df), 10, f"{sym} {tf}")

    def test_clean_years_only_and_pricedata_verified_every_timeframe(self):
        tfs = ("M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN")
        for sym in pricedata.SYMBOLS:
            cf = pricedata.clean_from(sym, REAL_PRICEDATA)
            self.assertEqual(cf, pd.Timestamp("2022-01-01"), sym)
            df = pricedata.load_frame(sym, "H4", root=REAL_PRICEDATA, clock="server")
            self.assertGreaterEqual(df.index[0], cf, sym)
            st = pricedata.verification_status(sym, tfs, REAL_PRICEDATA)
            self.assertEqual({tf: v["verified"] for tf, v in st.items()}, dict.fromkeys(tfs, True), sym)

    def test_pre_clean_years_are_price_verified_fx_only_and_match_m1(self):
        want = {"EURUSD": "2020-01-01", "GBPUSD": "2019-01-02", "USDJPY": "2020-01-01",
                "XAUUSD": None, "XAGUSD": None, "BTCUSD": None}
        for sym, first in want.items():
            got = pricedata.pre_clean_from(sym, REAL_PRICEDATA)
            self.assertEqual(got, pd.Timestamp(first) if first else None, sym)
        # 2021 EURUSD H1 (pre-clean) against the pre-clean M1: must match bar for bar
        h1 = pricedata.load_frame("EURUSD", "H1", root=REAL_PRICEDATA, pre_clean=True,
                                  start="2021-03-01", end="2021-06-01")
        self.assertGreater(h1.attrs["pre_clean_rows"], 1000)
        r = pricedata.check_vs_m1("EURUSD", {"H1": h1}, start="2021-03-01", end="2021-06-01",
                                  root=REAL_PRICEDATA)["H1"]
        self.assertEqual((r["status"], len(r["missing"]), len(r["mismatched"]), len(r["extra"])),
                         ("clean", 0, 0, 0))
        self.assertGreater(r["compared"], 1000)


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
        # The page opens on its own pre/post window (todo item 4).
        self.assertEqual(page["view"], {"from": b["time"][0], "to": b["time"][-1]})
        self.assertNotIn("netUnit", tr)                     # dollars by default

    def test_net_unit_from_rows(self):
        (s,) = _setups([self._row(exit_time=str(self.df.index[320]), exit_price=1.1,
                                  net=-1.07, net_unit="R")], "M15")
        self.assertEqual(s.net_unit, "R")
        self.assertEqual(setups.slice_spec(self.df, s)["overlay"]["trades"][0]["netUnit"], "R")
        with self.assertRaisesRegex(ValueError, "row 0.*net unit"):
            _setups([self._row(net=1.0, net_unit="points")], "M15")

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

    def test_page_windows_are_the_bars_the_page_shows(self):
        ss = _setups([self._row(), self._row(id="late", entry_time=str(self.df.index[500]),
                                             arm=str(self.df.index[499]))], "M15")
        extras = {"H1": self.h1, "D1": self.d1}
        for s, w in zip(ss, setups.page_windows(self.df, ss, 30, 20, extras)):
            page = setups.slice_spec(self.df, s, 30, 20, extra_frames=extras)
            self.assertEqual(set(w), set(page["timeframes"]))
            for tf, (a, b) in w.items():
                t = page["timeframes"][tf]["time"]
                self.assertEqual(export._epochs(pd.DatetimeIndex([a, b])), [t[0], t[-1]], tf)

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
