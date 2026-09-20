"""Tests for data sources, clocks and UTC conversion (FTMO server time / Dukascopy).

Needs pandas + numpy. The real-data classes read the actual FTMO priceData parquet
and the packaged Dukascopy library and skip themselves when those folders are absent.
"""
from __future__ import annotations

import datetime as dt
import gzip
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
if pd is not None:
    import test_pipeline  # noqa: F401  (registers the chartlab package)
    from chartlab import chart, pricedata, sources as S

REAL_PRICEDATA = Path(r"C:\personalCode\priceData")
DUKA_LIB = Path(r"C:\personalCode\mtf-regime-engine-v22.4\data\library")


def week_frame(weeks=4, freq="15min", start="2026-01-05", labels="server", attrs=None, spread=True):
    """Mon 00:00 -> Fri 23:45 server-time labels, no weekend (an FX/metal instrument)."""
    days = pd.date_range(start, periods=weeks * 7, freq="D")
    days = days[days.dayofweek < 5]
    idx = pd.DatetimeIndex(sorted(t for d in days for t in pd.date_range(d, periods=int(86400 / pd.Timedelta(freq).total_seconds()), freq=freq)),
                           name="Datetime")
    rng = np.random.default_rng(0)
    c = 1.1 + np.cumsum(rng.normal(0, 0.0002, len(idx)))
    o = np.r_[c[0], c[:-1]]
    df = pd.DataFrame({"Open": np.round(o, 5), "High": np.round(np.maximum(o, c) + 0.0001, 5),
                       "Low": np.round(np.minimum(o, c) - 0.0001, 5), "Close": np.round(c, 5),
                       "Volume": 100}, index=idx)
    if spread:
        df["SpreadPts"] = 3
    df.attrs = dict(attrs or {})
    return df


@unittest.skipIf(pd is None, "pandas/numpy not installed")
class TestFtmoClock(unittest.TestCase):
    def test_us_dst_bounds_are_the_us_switch_instants(self):
        for y, mar, nov in ((2019, "03-10", "11-03"), (2024, "03-10", "11-03"), (2026, "03-08", "11-01")):
            a, b = S._us_dst_bounds(y)
            self.assertEqual(str(a), f"{y}-{mar}T07:00:00.000000000")
            self.assertEqual(str(b), f"{y}-{nov}T06:00:00.000000000")
        edges = pd.DatetimeIndex(["2024-03-10 06:59:59", "2024-03-10 07:00:00",
                                  "2024-11-03 05:59:59", "2024-11-03 06:00:00"])
        self.assertEqual(S.in_us_dst(edges).tolist(), [False, True, True, False])

    def test_server_labels_convert_to_utc(self):
        cases = {"2026-01-15 00:00": "2026-01-14 22:00",     # winter: UTC+2
                 "2026-07-15 00:00": "2026-07-14 21:00",     # US summer: UTC+3
                 "2026-03-10 00:00": "2026-03-09 21:00"}     # US DST on, EU DST not yet: still UTC+3
        for label, utc in cases.items():
            self.assertEqual(S.ftmo_server_to_utc(pd.DatetimeIndex([label]))[0], pd.Timestamp(utc), label)

    def test_us_rule_not_eu_rule_in_the_weeks_they_disagree(self):
        # 2026-03-10 is after US DST (Mar 8) and before EU DST (Mar 29): an EU rule would say UTC+2
        eu_answer = pd.Timestamp("2026-03-09 22:00")
        self.assertNotEqual(S.ftmo_server_to_utc(pd.DatetimeIndex(["2026-03-10 00:00"]))[0], eu_answer)
        # 2026-10-28: EU DST already over (Oct 25), US DST still on (until Nov 1): still UTC+3
        self.assertEqual(S.ftmo_server_to_utc(pd.DatetimeIndex(["2026-10-28 00:00"]))[0],
                         pd.Timestamp("2026-10-27 21:00"))

    def test_round_trip_outside_the_repeated_hour(self):
        utc = pd.date_range("2019-01-01", "2026-12-31", freq="17min")
        back = S.ftmo_server_to_utc(S.utc_to_ftmo_server(utc))
        bad = [t for t, b in zip(utc, back) if t != b]
        for t in bad:      # the ONLY failures are the repeated autumn hour (its 2nd pass is ambiguous)
            a, b = S._us_dst_bounds(t.year)
            self.assertTrue(pd.Timestamp(b) <= t < pd.Timestamp(b) + pd.Timedelta(hours=1), t)

    def test_continuous_series_across_both_switches_stays_ordered(self):
        for y in (2024, 2025):
            utc = pd.date_range(f"{y}-03-08", f"{y}-03-12", freq="h").append(
                pd.date_range(f"{y}-11-01", f"{y}-11-05", freq="h"))
            labels = S.utc_to_ftmo_server(utc)
            keep = ~pd.Index(labels).duplicated(keep="first")      # what the dump has: the 2nd pass is absent
            back = S.ftmo_server_to_utc(labels[keep])
            self.assertTrue(back.is_monotonic_increasing and back.is_unique)
            self.assertTrue((back == utc[keep]).all())


@unittest.skipIf(pd is None, "pandas/numpy not installed")
class TestToUtc(unittest.TestCase):
    def test_ftmo_frame_is_converted_and_tagged(self):
        raw = week_frame(attrs={"source": "FTMO MT5", "timezone": "EET (server)"})
        u = S.to_utc(raw)
        self.assertEqual(u.index[0], pd.Timestamp("2026-01-04 22:00"))          # Mon 00:00 server = Sun 22:00Z
        self.assertEqual(u.attrs["source"], "ftmo")
        self.assertEqual(u.attrs["clock"], "UTC")
        self.assertEqual(u.attrs["utc_conversion"]["merged_stamps"], 0)
        self.assertTrue(u.index.is_monotonic_increasing and u.index.is_unique)
        self.assertEqual(u["Close"].tolist(), raw["Close"].tolist())            # only the labels move

    def test_input_frame_is_not_modified(self):
        raw = week_frame(attrs={"source": "FTMO MT5"})
        before = raw.index.copy()
        S.to_utc(raw)
        self.assertTrue(raw.index.equals(before))
        self.assertNotIn("clock", raw.attrs)

    def test_converting_twice_is_a_no_op_and_stripped_tag_is_refused(self):
        u = S.to_utc(week_frame(attrs={"source": "FTMO MT5"}))
        self.assertTrue(S.to_utc(u).index.equals(u.index))
        stripped = u.copy()
        stripped.attrs = {}
        with self.assertRaisesRegex(S.SourceError, "refusing"):
            S.to_utc(stripped)                    # has SpreadPts + Sunday bars: would be shifted twice

    def test_dukascopy_is_already_utc(self):
        d = week_frame(spread=False)
        d.index = S.ftmo_server_to_utc(d.index)                      # UTC labels, Sunday-evening open included
        d = d.rename(columns={c: f"bid_{c.lower()}" for c in ("Open", "High", "Low", "Close", "Volume")})
        d["ask_open"] = d["bid_open"] + 0.0001
        u = S.to_utc(d)                                              # identified as Dukascopy from bid_/ask_
        self.assertTrue(u.index.equals(d.index))
        self.assertEqual(u.attrs["source"], "dukascopy")
        self.assertEqual(u.attrs["utc_conversion"]["rule"], "already UTC")

    def test_tz_aware_index_is_normalised(self):
        df = week_frame(spread=False).drop(columns=[])
        df.index = df.index.tz_localize("UTC")
        u = S.to_utc(df, "dukascopy")
        self.assertIsNone(u.index.tz)
        self.assertEqual(u.index[0], pd.Timestamp("2026-01-05 00:00"))

    def test_bars_colliding_on_one_utc_stamp_are_merged_not_dropped(self):
        # BTCUSD at the 2024 spring switch: H1 labels 09:00 and 10:00 are both 07:00Z
        idx = pd.DatetimeIndex(["2024-03-10 08:00", "2024-03-10 09:00", "2024-03-10 10:00", "2024-03-10 11:00"], name="Datetime")
        df = pd.DataFrame({"Open": [1., 2., 3., 4.], "High": [1.5, 2.5, 3.9, 4.5], "Low": [.5, 1.9, 2.8, 3.5],
                           "Close": [1.2, 2.2, 3.3, 4.2], "Volume": [10, 20, 30, 40], "SpreadPts": [1, 2, 3, 4]}, index=idx)
        df.attrs = {"source": "FTMO MT5"}
        u = S.to_utc(df)
        self.assertEqual(len(u), 3)
        self.assertEqual(u.attrs["utc_conversion"]["merged_stamps"], 1)
        m = u.loc[pd.Timestamp("2024-03-10 07:00")]
        self.assertEqual((m["Open"], m["High"], m["Low"], m["Close"], m["Volume"]), (2.0, 3.9, 1.9, 3.3, 50))
        self.assertTrue(u.index.is_monotonic_increasing and u.index.is_unique)


@unittest.skipIf(pd is None, "pandas/numpy not installed")
class TestIdentification(unittest.TestCase):
    def test_tag_is_the_strongest_evidence(self):
        d = S.detect(week_frame(attrs={"source": "FTMO MT5", "timezone": "EET (server)"}))
        self.assertEqual((d.source, d.confidence, d.clock), ("ftmo", "high", "server"))
        duka = week_frame(spread=False)
        duka.attrs = {"source": "Dukascopy (website CSV, GMT)"}
        self.assertEqual((S.detect(duka).source, S.detect(duka).confidence), ("dukascopy", "high"))

    def test_column_shape_is_next(self):
        self.assertEqual((S.detect(week_frame()).source, S.detect(week_frame()).confidence), ("ftmo", "medium"))
        bid = week_frame(spread=False)
        bid = bid.rename(columns={c: f"bid_{c.lower()}" for c in ("Open", "High", "Low", "Close", "Volume")})
        bid["ask_open"] = bid["bid_open"] + 0.0001
        d = S.detect(bid)
        self.assertEqual((d.source, d.confidence, d.clock), ("dukascopy", "medium", "utc"))

    def test_weekend_fingerprint_separates_server_labels_from_utc_labels(self):
        server = week_frame(spread=False)
        as_utc = server.copy()
        as_utc.index = S.ftmo_server_to_utc(server.index)
        fs, fu = S.weekend_fingerprint(server.index), S.weekend_fingerprint(as_utc.index)
        self.assertEqual(fs["kind"], "weekday_only")
        self.assertEqual(fu["kind"], "utc_like")
        self.assertGreater(fu["sun"], 0.005)

    def test_bare_utc_frame_is_dukascopy_low_and_bare_server_frame_is_undecidable(self):
        server = week_frame(spread=False)
        as_utc = server.copy()
        as_utc.index = S.ftmo_server_to_utc(server.index)
        d = S.detect(as_utc)
        self.assertEqual((d.source, d.confidence, d.clock), ("dukascopy", "low", "utc"))
        d0 = S.detect(server)
        self.assertIsNone(d0.source)
        with self.assertRaisesRegex(S.SourceError, "cannot tell"):
            S.resolve(server)
        self.assertEqual(S.resolve(server, "ftmo").clock, "server")      # ...unless the caller declares it

    def test_a_declared_source_that_contradicts_the_frame_is_refused(self):
        with self.assertRaisesRegex(S.SourceError, "declared source 'dukascopy'"):
            S.resolve(week_frame(attrs={"source": "FTMO MT5"}), "dukascopy")
        as_utc = week_frame(spread=False)
        as_utc.index = S.ftmo_server_to_utc(as_utc.index)
        with self.assertRaisesRegex(S.SourceError, "refusing"):
            S.to_utc(as_utc, "ftmo")               # says FTMO, but Sunday bars prove the labels are UTC
        with self.assertRaises(S.SourceError):
            S.resolve(week_frame(), "nonsense")

    def test_crypto_and_short_or_weekly_frames_have_no_fingerprint(self):
        self.assertEqual(S.weekend_fingerprint(pd.date_range("2026-01-01", periods=2000, freq="h"))["kind"], "trades_weekends")
        self.assertEqual(S.weekend_fingerprint(week_frame(weeks=1).index)["kind"], "n/a")
        self.assertEqual(S.weekend_fingerprint(pd.date_range("2020-01-05", periods=200, freq="7D"))["kind"], "n/a")

    def test_timeframe_names(self):
        self.assertEqual(S.timeframe_name(week_frame(freq="15min").index), "M15")
        self.assertEqual(S.timeframe_name(week_frame(freq="1h").index), "H1")
        self.assertIsNone(S.timeframe_name(pd.date_range("2026-01-01", periods=10, freq="7min")))


@unittest.skipIf(pd is None, "pandas/numpy not installed")
class TestOverlayTimes(unittest.TestCase):
    def test_clocks(self):
        f = S.to_utc_epoch
        utc = int(pd.Timestamp("2026-01-15 10:00", tz="UTC").timestamp())
        self.assertEqual(f("2026-01-15 10:00", "utc"), utc)
        self.assertEqual(f("2026-01-15 18:00", "myt"), utc)                       # MYT = UTC+8
        self.assertEqual(f("2026-01-15 12:00", "ftmo"), utc)                      # winter server = UTC+2
        self.assertEqual(f("2026-07-15 13:00", "ftmo"), int(pd.Timestamp("2026-07-15 10:00", tz="UTC").timestamp()))
        self.assertEqual(f(pd.Timestamp("2026-01-15 12:00"), "ftmo"), utc)
        self.assertEqual(f(int(pd.Timestamp("2026-01-15 12:00", tz="UTC").timestamp()), "ftmo"), utc)   # MT5-style epoch

    def test_a_time_carrying_its_own_zone_is_absolute(self):
        utc = int(pd.Timestamp("2026-01-15 10:00", tz="UTC").timestamp())
        for clock in ("utc", "myt", "ftmo"):
            self.assertEqual(S.to_utc_epoch("2026-01-15T10:00:00Z", clock), utc)
            self.assertEqual(S.to_utc_epoch("2026-01-15T18:00:00+08:00", clock), utc)
            self.assertEqual(S.to_utc_epoch(dt.datetime(2026, 1, 15, 18, tzinfo=dt.timezone(dt.timedelta(hours=8))), clock), utc)

    def test_bad_input(self):
        with self.assertRaises(S.ClockError):
            S.to_utc_epoch("2026-01-15 10:00", "gmt+9")
        with self.assertRaises(TypeError):
            S.to_utc_epoch(True)
        with self.assertRaises(TypeError):
            S.to_utc_epoch(object())

    def test_overlay_needs_a_clock_and_converts_every_time_field(self):
        trades = [{"dir": "long", "entry_time": "2026-01-15 12:00", "entryPrice": 1.1, "exit_time": "2026-01-15 14:00"}]
        zones = [{"from": "2026-01-15 12:00", "to": "2026-01-15 13:00", "lo": 1.0, "hi": 1.2}]
        equity = {"time": ["2026-01-15 12:00"], "value": [100.0]}
        with self.assertRaises(S.ClockError):
            S.convert_overlay(trades, zones, equity, None)
        t, z, e = S.convert_overlay(trades, zones, equity, "ftmo")
        base = int(pd.Timestamp("2026-01-15 10:00", tz="UTC").timestamp())
        self.assertEqual((t[0]["entry_time"], t[0]["exit_time"]), (base, base + 7200))
        self.assertEqual((z[0]["from"], z[0]["to"]), (base, base + 3600))
        self.assertEqual(e["time"], [base])
        self.assertEqual(trades[0]["entry_time"], "2026-01-15 12:00")           # inputs untouched
        self.assertEqual(S.convert_overlay(None, None, None, None), (None, None, None))   # nothing to convert: no clock needed


@unittest.skipIf(pd is None, "pandas/numpy not installed")
class TestFiles(unittest.TestCase):
    def test_dukascopy_website_csv_is_recognised_as_utc(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "EURUSD_M15.csv"
            p.write_text("Gmt time,Open,High,Low,Close,Volume\n"
                         "05.01.2026 00:00:00.000,1.1,1.2,1.0,1.15,10\n"
                         "05.01.2026 00:15:00.000,1.15,1.25,1.1,1.2,12\n", encoding="utf-8")
            df = S.load_file(p)
            self.assertEqual(df.index[0], pd.Timestamp("2026-01-05 00:00"))
            d = S.detect(df)
            self.assertEqual((d.source, d.clock, d.confidence), ("dukascopy", "utc", "high"))

    def test_parquet_with_a_time_column_and_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                import pyarrow  # noqa: F401
            except ImportError:
                self.skipTest("pyarrow not installed")
            p = Path(tmp) / "x.parquet"
            pd.DataFrame({"time": pd.date_range("2026-01-05", periods=3, freq="h"), "Open": 1., "High": 2., "Low": .5,
                          "Close": 1.5}).to_parquet(p, index=False)
            self.assertEqual(S.load_file(p).index[1], pd.Timestamp("2026-01-05 01:00"))
            with self.assertRaises(FileNotFoundError):
                S.load_file(Path(tmp) / "nope.csv")
            bad = Path(tmp) / "bad.csv"
            bad.write_text("a,b\n1,2\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no time column"):
                S.load_file(bad)


@unittest.skipIf(pd is None or not (REAL_PRICEDATA / "data" / "clean").is_dir(), "real priceData folder not present")
class TestRealFtmoData(unittest.TestCase):
    def test_every_symbol_converts_to_a_clean_utc_series(self):
        for sym in pricedata.SYMBOLS:
            for tf in ("M15", "H1", "D1", "W1"):
                u = pricedata.load_frame(sym, tf, root=REAL_PRICEDATA)
                self.assertTrue(u.index.is_monotonic_increasing and u.index.is_unique, f"{sym} {tf}")
                self.assertEqual(u.attrs["source"], "ftmo")
                merged = u.attrs["utc_conversion"]["merged_stamps"]
                if sym != "BTCUSD":
                    self.assertEqual(merged, 0, f"{sym} {tf}: only BTCUSD trades through a DST switch")

    def test_real_files_are_identified_from_their_own_tag(self):
        for sym, tf in (("EURUSD", "M15"), ("XAUUSD", "H4"), ("USDJPY", "D1"), ("BTCUSD", "H1"), ("EURUSD", "W1")):
            raw = pd.read_parquet(REAL_PRICEDATA / "data" / "clean" / sym / f"{sym}_{tf}.parquet")
            d = S.detect(raw)
            self.assertEqual((d.source, d.confidence, d.clock), ("ftmo", "high", "server"), f"{sym} {tf}")

    def test_no_real_fx_or_metal_frame_has_a_weekend_bar_in_server_labels(self):
        for sym in ("EURUSD", "XAUUSD", "USDJPY"):
            for tf in ("M5", "H1", "H4", "D1"):
                idx = pd.read_parquet(REAL_PRICEDATA / "data" / "clean" / sym / f"{sym}_{tf}.parquet", columns=["Close"]).index
                self.assertEqual(S.weekend_fingerprint(idx)["kind"], "weekday_only", f"{sym} {tf}")
                after = S.weekend_fingerprint(S.ftmo_server_to_utc(idx))
                self.assertEqual(after["kind"], "utc_like", f"{sym} {tf}: UTC labels must show the Sunday-evening open")

    def test_daily_bars_open_at_17_new_york_after_conversion(self):
        for sym in pricedata.SYMBOLS:
            d1 = pricedata.load_frame(sym, "D1", root=REAL_PRICEDATA)
            ny = d1.index.tz_localize("UTC").tz_convert("America/New_York")
            self.assertEqual(set(ny.hour), {17}, sym)       # every D1 bar opens at 17:00 New York, DST or not


@unittest.skipIf(pd is None or not (REAL_PRICEDATA / "data" / "clean").is_dir() or not DUKA_LIB.is_dir(),
                 "priceData and the Dukascopy library are both needed")
class TestAgainstRealDukascopy(unittest.TestCase):
    """The independent proof: converted FTMO H1 must match REAL Dukascopy H4 at the same UTC instant."""

    @staticmethod
    def _lib(sym, tf):
        rows = json.load(gzip.open(DUKA_LIB / f"{sym}_{tf}.json.gz", "rt"))["rows"]
        df = pd.DataFrame(rows)
        df.index = pd.DatetimeIndex(pd.to_datetime(df["t"]).dt.tz_localize(None), name="t")
        return df

    @staticmethod
    def _score(lib, ftmo_h1_index_utc, h1, windows):
        h1 = h1.copy()
        h1.index = ftmo_h1_index_utc
        agg = h1.groupby(h1.index.floor("4h")).agg(o=("Open", "first"), c=("Close", "last"), n=("Close", "size"))
        j = lib.join(agg, rsuffix="_f", how="inner")
        j = j[j["n"] == 4]
        if windows is not None:
            keep = np.zeros(len(j), dtype=bool)
            for lo, hi in windows:
                keep |= (j.index >= lo) & (j.index < hi)
            j = j[keep]
        rng = (j["h"] - j["l"]).replace(0, np.nan)
        return len(j), float(((j["c"] - j["c_f"]).abs() / rng).median())

    def test_gbpusd_us_rule_matches_dukascopy_and_eu_rule_does_not(self):
        lib = self._lib("GBPUSD", "H4")
        h1 = pd.read_parquet(REAL_PRICEDATA / "data" / "clean" / "GBPUSD" / "GBPUSD_H1.parquet")

        def nth_sunday(y, m, n):
            d = pd.Timestamp(y, m, 1)
            return d + pd.Timedelta(days=(6 - d.weekday()) % 7) + pd.Timedelta(weeks=n - 1)

        def last_sunday(y, m):
            d = pd.Timestamp(y, m, 1) + pd.offsets.MonthEnd(0)
            return d - pd.Timedelta(days=(d.weekday() + 1) % 7)

        windows = []
        for y in range(2019, 2027):        # the weeks where the US and EU calendars disagree
            windows += [(nth_sunday(y, 3, 2) + pd.Timedelta(days=1), last_sunday(y, 3)),
                        (last_sunday(y, 10), nth_sunday(y, 11, 1))]
        idx = h1.index
        eu_off = np.full(len(idx), 2)
        for y in np.unique(idx.year):       # EU rule: UTC+3 from last Sun Mar 01:00Z to last Sun Oct 01:00Z
            a, b = last_sunday(y, 3) + pd.Timedelta(hours=1), last_sunday(y, 10) + pd.Timedelta(hours=1)
            m = (idx.year == y) & (idx - pd.Timedelta(hours=3) >= a) & (idx - pd.Timedelta(hours=3) < b)
            eu_off[m] = 3
        eu_index = idx - pd.to_timedelta(eu_off, unit="h")

        n_us, us = self._score(lib, S.ftmo_server_to_utc(idx), h1, windows)
        n_eu, eu = self._score(lib, eu_index, h1, windows)
        self.assertGreater(n_us, 400)
        self.assertLess(us, 0.02, "US rule should sit within 2% of the bar range")
        self.assertGreater(eu, 5 * us, "EU rule must be clearly worse where the calendars disagree")
        _, overall = self._score(lib, S.ftmo_server_to_utc(idx), h1, None)
        self.assertLess(overall, 0.012)

    def test_real_dukascopy_frames_are_identified_by_their_own_weekend_fingerprint(self):
        for sym in ("XAUUSD", "GBPUSD"):
            for tf in ("H4", "D1"):
                df = self._lib(sym, tf).rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
                df = df[["Open", "High", "Low", "Close", "Volume"]]
                d = S.detect(df)
                self.assertEqual((d.source, d.clock), ("dukascopy", "utc"), f"{sym} {tf}")
                self.assertGreater(S.weekend_fingerprint(df.index)["sun"], 0.03)
                self.assertTrue(S.to_utc(df, "dukascopy").index.equals(df.index))      # left untouched


if __name__ == "__main__":
    unittest.main()
