"""Tests for the standalone ChartLab generator (stdlib only).

Run from the repository root::

    python -m unittest discover -s tests -v

or directly::

    python tests/test_chart.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chart  # noqa: E402


def _bars(n=3, start=1704067200):
    return {
        "time": [start + i * 86400 for i in range(n)],
        "open": [2050.0 + i for i in range(n)],
        "high": [2056.0 + i for i in range(n)],
        "low": [2048.0 + i for i in range(n)],
        "close": [2054.0 + i for i in range(n)],
    }


class TestTime(unittest.TestCase):
    def test_epoch_passthrough(self):
        self.assertEqual(chart.to_epoch(1704067200), 1704067200)

    def test_iso_string(self):
        self.assertEqual(chart.to_epoch("2024-01-01T00:00:00Z"), 1704067200)

    def test_naive_datetime_is_utc(self):
        dt = datetime(2024, 1, 1, 0, 0, 0)
        self.assertEqual(chart.to_epoch(dt), 1704067200)

    def test_aware_datetime(self):
        dt = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(chart.to_epoch(dt), 1704067200)

    def test_numeric_string(self):
        self.assertEqual(chart.to_epoch("1704067200"), 1704067200)

    def test_bool_rejected(self):
        with self.assertRaises(TypeError):
            chart.to_epoch(True)


class TestNormalization(unittest.TestCase):
    def test_bars_from_dict_rows(self):
        rows = [
            {"time": 1704067200, "open": 1, "high": 2, "low": 0, "close": 1.5},
            {"time": 1704153600, "open": 1.5, "high": 3, "low": 1, "close": 2},
        ]
        block = chart.bars_from_rows(rows)
        self.assertEqual(block["time"], [1704067200, 1704153600])
        self.assertEqual(block["high"], [2.0, 3.0])
        self.assertNotIn("volume", block)

    def test_bars_from_sequence_rows(self):
        rows = [(1704067200, 1, 2, 0, 1.5)]
        block = chart.bars_from_rows(rows)
        self.assertEqual(block["close"], [1.5])

    def test_compact_block_keys(self):
        out = chart.spec("X", {"D1": {"t": [1704067200], "o": [1.0], "h": [2.0],
                                        "l": [0.5], "c": [1.5]}})
        self.assertEqual(out["timeframes"]["D1"]["close"], [1.5])

    def test_trade_aliases(self):
        trades = chart.trades_from_rows([
            {"direction": "buy", "entry_time": 1704067200, "entry_price": 2050,
             "stop": 2040, "target": 2070, "net": 100},
        ])
        t = trades[0]
        self.assertEqual(t["dir"], "long")
        self.assertEqual(t["entryTime"], 1704067200)
        self.assertEqual(t["sl"], 2040.0)
        self.assertEqual(t["tp"], 2070.0)

    def test_zone_aliases(self):
        zones = chart.zones_from_rows([
            {"from": 1704067200, "to": 1704326400, "lo": 2048, "hi": 2066, "name": "Z"},
        ])
        z = zones[0]
        self.assertEqual(z["start"], 1704067200)
        self.assertEqual(z["end"], 1704326400)
        self.assertEqual(z["low"], 2048.0)
        self.assertEqual(z["high"], 2066.0)
        self.assertEqual(z["label"], "Z")

    def test_indicators_from_rows(self):
        inds = chart.indicators_from_rows([
            {"name": "SMA3", "type": "sma", "period": 3, "values": [1, 2, 3], "on": False},
        ])
        self.assertEqual(inds[0]["period"], 3)
        self.assertIs(inds[0]["on"], False)


class TestSpecAndValidate(unittest.TestCase):
    def test_spec_defaults_to_largest_tf(self):
        s = chart.spec("XAUUSD", {"D1": _bars(3), "H4": _bars(5)},
                       default_tf=None, trades=[], zones=[])
        self.assertEqual(s["defaultTimeframe"], "H4")
        self.assertEqual(s["version"], 1)

    def test_spec_rejects_unknown_default(self):
        with self.assertRaises(ValueError):
            chart.spec("X", {"D1": _bars(2)}, default_tf="W1")

    def test_validate_clean(self):
        s = chart.spec("X", {"D1": _bars(3)})
        self.assertEqual(chart.validate(s), [])

    def test_validate_length_mismatch(self):
        s = chart.spec("X", {"D1": _bars(3)})
        s["timeframes"]["D1"]["close"].pop()
        problems = chart.validate(s)
        self.assertTrue(any("close length" in p for p in problems))

    def test_validate_unsorted_time(self):
        s = chart.spec("X", {"D1": _bars(3)})
        s["timeframes"]["D1"]["time"] = [3, 1, 2]
        self.assertTrue(any("not ascending" in p for p in chart.validate(s)))

    def test_validate_missing_default(self):
        s = chart.spec("X", {"D1": _bars(3)})
        s["defaultTimeframe"] = "W1"
        self.assertTrue(any("defaultTimeframe" in p for p in chart.validate(s)))


class TestRender(unittest.TestCase):
    def test_render_writes_page_and_lib(self):
        s = chart.spec("XAUUSD", {"D1": _bars(3)}, period_label="D1 · XAUUSD")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "page.html"
            chart.render(s, out, title="Test Page")
            html = out.read_text(encoding="utf-8")
            self.assertIn("<title>Test Page</title>", html)
            self.assertIn(str(1704067200), html)
            self.assertTrue((Path(tmp) / "lib" / chart.LIB_NAME).exists())

    def test_render_inline_lib(self):
        s = chart.spec("X", {"D1": _bars(2)})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "p.html"
            chart.render(s, out, inline_lib=True)
            html = out.read_text(encoding="utf-8")
            self.assertIn("data:text/javascript;base64,", html)
            self.assertFalse((Path(tmp) / "lib").exists())

    def test_render_file_roundtrip(self):
        s = chart.spec("X", {"D1": _bars(3)})
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "s.json"
            spec_path.write_text(json.dumps(s), encoding="utf-8")
            out = chart.render_file(spec_path, Path(tmp) / "o.html")
            self.assertTrue(out.exists())


class TestCsv(unittest.TestCase):
    def _write(self, path, text):
        Path(path).write_text(text, encoding="utf-8")

    def test_bars_trades_equity_from_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            bars_csv = Path(tmp) / "bars.csv"
            self._write(bars_csv, "date,open,high,low,close\n"
                                  "2024-01-01T00:00:00Z,1,2,0.5,1.5\n"
                                  "2024-01-02T00:00:00Z,1.5,2.5,1,2\n")
            block = chart.bars_from_csv(bars_csv)
            self.assertEqual(block["time"], [1704067200, 1704153600])
            self.assertEqual(block["open"], [1.0, 1.5])

            trades_csv = Path(tmp) / "t.csv"
            self._write(trades_csv, "dir,entry_time,entry_price,exit_time,exit_price,net\n"
                                    "long,2024-01-01T00:00:00Z,2050,2024-01-02T00:00:00Z,2060,100\n")
            t = chart.trades_from_csv(trades_csv)[0]
            self.assertEqual(t["dir"], "long")
            self.assertEqual(t["entryPrice"], 2050.0)

            eq_csv = Path(tmp) / "e.csv"
            self._write(eq_csv, "time,equity\n2024-01-01T00:00:00Z,100000\n"
                                "2024-01-02T00:00:00Z,100500\n")
            eq = chart.equity_from_csv(eq_csv)
            self.assertEqual(eq["value"], [100000.0, 100500.0])


class TestGalleryAndIndicators(unittest.TestCase):
    def test_gallery_lists_pages(self):
        s = chart.spec("X", {"D1": _bars(3)})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chart.render(s, root / "a.html", title="Alpha")
            idx = chart.gallery(root, title="Charts")
            html = idx.read_text(encoding="utf-8")
            self.assertIn("a.html", html)

    def test_parse_indicators(self):
        inds = chart.parse_indicators("SMA50, EMA200 ,BB20")
        self.assertEqual([i["type"] for i in inds], ["sma", "ema", "bb"])
        self.assertEqual(inds[0]["period"], 50)
        self.assertEqual(inds[1]["period"], 200)
        self.assertEqual(inds[2]["period"], 20)


class TestNormalize(unittest.TestCase):
    def test_normalize_iso_and_aliases(self):
        raw = {
            "symbol": "X",
            "timeframes": {"D1": {"time": ["2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z"],
                                  "o": [1, 2], "h": [2, 3], "l": [0.5, 1], "c": [1.5, 2]}},
            "overlay": {"trades": [{"side": "buy", "time": "2024-01-01T00:00:00Z",
                                    "price": 1.0, "exit_time": "2024-01-02T00:00:00Z",
                                    "exit_price": 2.0}]},
        }
        s = chart.normalize(raw)
        self.assertEqual(s["timeframes"]["D1"]["time"], [1704067200, 1704153600])
        self.assertEqual(s["defaultTimeframe"], "D1")
        self.assertEqual(s["overlay"]["trades"][0]["dir"], "long")
        self.assertEqual(s["overlay"]["trades"][0]["entryTime"], 1704067200)
        self.assertEqual(chart.validate(s), [])

    def test_normalize_does_not_mutate_input(self):
        raw = {"symbol": "X", "timeframes": {"D1": {"t": [1], "o": [1], "h": [1],
                                                     "l": [1], "c": [1]}}}
        chart.normalize(raw)
        self.assertIn("t", raw["timeframes"]["D1"])


class TestRenderSafety(unittest.TestCase):
    def test_payload_escapes_script_tag(self):
        s = {"symbol": "</script><b>x", "timeframes": {"D1": _bars(1)}}
        with tempfile.TemporaryDirectory() as tmp:
            out = chart.render(s, Path(tmp) / "p.html")
            html = out.read_text(encoding="utf-8")
            self.assertNotIn("</script><b>", html)
            self.assertIn("\\u003c/script", html)

    def test_render_accepts_raw_iso_spec(self):
        raw = {"symbol": "X",
               "timeframes": {"D1": {"time": ["2024-01-01T00:00:00Z"],
                                     "o": [1], "h": [1], "l": [1], "c": [1]}}}
        with tempfile.TemporaryDirectory() as tmp:
            out = chart.render(raw, Path(tmp) / "p.html")
            html = out.read_text(encoding="utf-8")
            self.assertIn("1704067200", html)
            self.assertNotIn("2024-01-01T00:00:00Z", html)


class TestStats(unittest.TestCase):
    def test_stats_from_list_and_dict(self):
        rows = chart.stats_from_rows([
            {"name": "Return", "value": "+22.59%", "tone": "good"},
            ["Max DD", -22.51],
        ])
        self.assertEqual(rows[0], {"label": "Return", "value": "+22.59%", "tone": "up"})
        self.assertEqual(rows[1]["label"], "Max DD")
        self.assertEqual(rows[1]["value"], -22.51)
        d = chart.stats_from_rows({"Trades": 117})
        self.assertEqual(d, [{"label": "Trades", "value": 117}])

    def test_spec_and_normalize_carry_stats(self):
        s = chart.spec("X", {"D1": _bars(2)}, stats=[{"label": "N", "value": 1}])
        self.assertEqual(s["stats"], [{"label": "N", "value": 1}])
        raw = {"symbol": "X", "timeframes": {"D1": {"t": [1], "o": [1], "h": [1],
                                                     "l": [1], "c": [1]}},
               "stats": {"PF": 1.23}}
        self.assertEqual(chart.normalize(raw)["stats"], [{"label": "PF", "value": 1.23}])


class TestCompact(unittest.TestCase):
    def _decode_u32(self, b):
        raw = __import__("base64").b64decode(b)
        return list(__import__("struct").unpack("<%dI" % (len(raw) // 4), raw))

    def _decode_i32(self, b, scale):
        raw = __import__("base64").b64decode(b)
        return [x / scale for x in __import__("struct").unpack("<%di" % (len(raw) // 4), raw)]

    def test_encode_block_roundtrip(self):
        block = _bars(3)
        enc = chart.encode_block(block)
        self.assertEqual(self._decode_u32(enc["t"]), block["time"])
        self.assertEqual(self._decode_i32(enc["o"], enc["s"]), block["open"])
        self.assertEqual(self._decode_i32(enc["c"], enc["s"]), block["close"])

    def test_compact_page_is_smaller(self):
        s = chart.spec("X", {"D1": _bars(50)})
        with tempfile.TemporaryDirectory() as tmp:
            plain = chart.render(s, Path(tmp) / "plain.html")
            small = chart.render(s, Path(tmp) / "small.html", compact=True)
            self.assertLess(small.stat().st_size, plain.stat().st_size)
            html = small.read_text(encoding="utf-8")
            self.assertIn('"t":"', html)
            self.assertNotIn('"time":[', html)


def _fx_bars(n=5, start=1704067200):
    """5-digit FX prices, the case a fixed 4 dp rounding destroys."""
    base = [1.14454, 1.14463, 1.14438, 1.14448, 1.14446]
    return {
        "time": [start + i * 900 for i in range(n)],
        "open": [base[i % 5] for i in range(n)],
        "high": [base[i % 5] + 0.00017 for i in range(n)],
        "low": [base[i % 5] - 0.00013 for i in range(n)],
        "close": [base[(i + 1) % 5] for i in range(n)],
    }


class TestPrecision(unittest.TestCase):
    def test_inferred_per_instrument(self):
        self.assertEqual(chart.infer_precision([1.14585, 1.1459]), 5)     # 5-digit FX
        self.assertEqual(chart.infer_precision([158.967, 159.0]), 3)      # JPY
        self.assertEqual(chart.infer_precision([4290.95, 4300.0]), 2)     # gold
        self.assertEqual(chart.infer_precision([126186.8]), 2)            # floor is 2

    def test_floor_cap_and_nulls(self):
        self.assertEqual(chart.infer_precision([2050.0, 2051.0]), 2)
        self.assertEqual(chart.infer_precision([1.123456789012]), chart.MAX_PRECISION)
        self.assertEqual(chart.infer_precision([None, float("nan"), 1.25]), 2)

    def test_float_noise_is_not_precision(self):
        # 1.1 + 2.2 style noise must not read as 15 decimals
        self.assertEqual(chart.infer_precision([0.1 + 0.2, 1.1 + 2.2]), 2)

    def test_scans_every_value_not_a_sample(self):
        vals = [1.5] * 10000 + [1.23456]
        self.assertEqual(chart.infer_precision(vals), 5)

    def test_spec_carries_and_normalize_infers(self):
        s = chart.spec("EURUSD", {"M15": _fx_bars()})
        self.assertEqual(s["precision"], 5)
        raw = {"symbol": "X", "timeframes": {"M15": _fx_bars()}}
        self.assertEqual(chart.normalize(raw)["precision"], 5)
        self.assertEqual(chart.spec("X", {"D1": _bars(3)})["precision"], 2)

    def test_explicit_precision_respected_and_checked(self):
        self.assertEqual(chart.spec("X", {"D1": _bars(3)}, precision=4)["precision"], 4)
        raw = {"symbol": "X", "precision": 3, "timeframes": {"D1": _bars(3)}}
        self.assertEqual(chart.normalize(raw)["precision"], 3)
        for bad in (-1, 9, 2.5, "5", True):
            with self.assertRaises(ValueError):
                chart.spec("X", {"D1": _bars(3)}, precision=bad)

    def test_validate_flags_bad_precision(self):
        s = chart.spec("X", {"D1": _bars(3)})
        s["precision"] = 12
        self.assertTrue(any("precision" in p for p in chart.validate(s)))

    def test_compact_is_lossless_for_5_digit_fx(self):
        block = _fx_bars()
        enc = chart.encode_block(block)
        self.assertEqual(enc["s"], 100000)
        dec = TestCompact()._decode_i32
        for key, short in (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c")):
            self.assertEqual(dec(enc[short], enc["s"]), block[key])

    def test_compact_page_round_trips_spec_precision(self):
        s = chart.spec("EURUSD", {"M15": _fx_bars()})
        with tempfile.TemporaryDirectory() as tmp:
            html = chart.render(s, Path(tmp) / "p.html", compact=True).read_text(encoding="utf-8")
        self.assertIn('"precision":5', html)
        self.assertIn('"s":100000', html)

    def test_compact_headroom_for_big_prices(self):
        # BTC above the old fixed-1e4 int32 ceiling (214,748) still encodes
        block = {"time": [1], "open": [250000.5], "high": [250001.0], "low": [249999.0],
                 "close": [250000.0]}
        enc = chart.encode_block(block)
        self.assertEqual(TestCompact()._decode_i32(enc["o"], enc["s"]), [250000.5])

    def test_compact_overflow_is_a_clear_error_not_silent_rounding(self):
        block = {"time": [1], "open": [250000.123456], "high": [250000.123456],
                 "low": [250000.123456], "close": [250000.123456]}
        with self.assertRaises(ValueError) as ctx:
            chart.encode_block(block)
        self.assertIn("compact", str(ctx.exception))

    def test_viewer_uses_precision_everywhere(self):
        html = (chart.ASSETS / chart.VIEWER_NAME).read_text(encoding="utf-8")
        self.assertIn("priceFormat:priceFmt()", html)      # candles + indicator lines
        self.assertIn("b.s||1e4", html)                    # per-block scale, old pages still decode
        self.assertNotIn("toFixed(3)", html)               # the hard-coded 3-decimal readouts
        self.assertIn("inferPrec", html)                   # specs without a precision field


class TestDisplayTimezone(unittest.TestCase):
    """Spec times are always UTC; ``tz`` is only how the viewer shows them."""

    def test_default_is_malaysian_time_and_data_stays_utc(self):
        s = chart.spec("X", {"D1": _bars(3)})
        self.assertEqual(s["tz"], "MYT")
        self.assertEqual(s["timeframes"]["D1"]["time"][0], 1704067200)     # untouched, not shifted +8h
        self.assertEqual(chart.spec("X", {"D1": _bars(3)}, tz="UTC")["tz"], "UTC")

    def test_source_is_recorded_only_when_given(self):
        self.assertNotIn("source", chart.spec("X", {"D1": _bars(3)}))
        self.assertEqual(chart.spec("X", {"D1": _bars(3)}, source="dukascopy")["source"], "dukascopy")

    def test_normalize_fills_tz_and_keeps_source(self):
        raw = {"symbol": "X", "source": "ftmo", "timeframes": {"D1": _bars(3)}}
        n = chart.normalize(raw)
        self.assertEqual((n["tz"], n["source"]), ("MYT", "ftmo"))
        self.assertEqual(chart.normalize(dict(raw, tz="UTC"))["tz"], "UTC")

    def test_bad_tz_is_refused_everywhere(self):
        with self.assertRaises(ValueError):
            chart.spec("X", {"D1": _bars(3)}, tz="EST")
        with self.assertRaises(ValueError):
            chart.normalize({"symbol": "X", "tz": "GMT+9", "timeframes": {"D1": _bars(3)}})
        s = chart.spec("X", {"D1": _bars(3)})
        s["tz"] = "EST"
        self.assertTrue(any("tz must be" in p for p in chart.validate(s)))

    def test_to_iso_displays_in_the_requested_zone(self):
        self.assertEqual(chart.to_iso(1704067200), "2024-01-01 00:00")
        self.assertEqual(chart.to_iso(1704067200, "MYT"), "2024-01-01 08:00")
        self.assertEqual(chart.to_iso(1704067200, "MYT", True), "2024-01-01 08:00 MYT")
        self.assertEqual(chart.to_iso(1704067200 - 1, "MYT"), "2024-01-01 07:59")
        with self.assertRaises(ValueError):
            chart.to_iso(0, "PST")

    def test_the_page_carries_tz_and_the_viewer_can_switch_it(self):
        s = chart.spec("X", {"D1": _bars(3)}, tz="UTC", source="ftmo")
        with tempfile.TemporaryDirectory() as tmp:
            html = chart.render(s, Path(tmp) / "p.html").read_text(encoding="utf-8")
        self.assertIn('"tz":"UTC"', html)
        self.assertIn('"source":"ftmo"', html)
        for needle in ('data-tz="MYT"', 'data-tz="UTC"', "function switchTZ", "chartlab.tz",
                       '"MYT":28800', "URLSearchParams(location.search).get(\"tz\")"):
            self.assertIn(needle, html, needle)

    def test_viewer_shifts_display_only_and_keeps_a_utc_offset_handle(self):
        html = (chart.ASSETS / chart.VIEWER_NAME).read_text(encoding="utf-8")
        self.assertIn("function shiftTimes", html)            # one in-place shift of every time array
        self.assertIn("tzOffset:OFF", html)                   # debug() exposes the applied offset


class TestLoader(unittest.TestCase):
    def test_render_loader_embeds_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = chart.render_loader(Path(tmp) / "l.html", "specs/xau.json")
            html = out.read_text(encoding="utf-8")
            self.assertIn('"specs/xau.json"', html)
            self.assertIn("lib/" + chart.LIB_NAME, html)
            self.assertNotIn("__SPEC_URL__", html)
            self.assertIn('<script id="payload" type="application/json">null</script>', html)


class TestValidationHardening(unittest.TestCase):
    def test_null_ohlc_flagged(self):
        s = chart.spec("X", {"D1": _bars(3)})
        s["timeframes"]["D1"]["close"][1] = None
        self.assertTrue(any("null/NaN" in p for p in chart.validate(s)))

    def test_nan_ohlc_flagged(self):
        s = chart.spec("X", {"D1": _bars(3)})
        s["timeframes"]["D1"]["high"][1] = float("nan")
        self.assertTrue(any("null/NaN" in p for p in chart.validate(s)))

    def test_duplicate_timestamps_flagged(self):
        s = chart.spec("X", {"D1": _bars(3)})
        s["timeframes"]["D1"]["time"][2] = s["timeframes"]["D1"]["time"][1]
        self.assertTrue(any("duplicate" in p for p in chart.validate(s)))

    def test_equity_length_mismatch_flagged(self):
        s = chart.spec("X", {"D1": _bars(2)},
                       equity={"time": [1, 2, 3], "value": [1.0, 2.0]})
        self.assertTrue(any("equity length" in p for p in chart.validate(s)))

    def test_indicator_length_mismatch_flagged(self):
        s = chart.spec("X", {"D1": _bars(3)},
                       indicators=[{"name": "SMA", "values": [1, 2]}])
        self.assertTrue(any("SMA values length" in p for p in chart.validate(s)))

    def test_valid_spec_still_clean(self):
        self.assertEqual(chart.validate(chart.spec("X", {"D1": _bars(3)})), [])


class TestEncodeHardening(unittest.TestCase):
    def test_null_price_raises(self):
        block = _bars(2)
        block["close"][1] = None
        with self.assertRaises(ValueError):
            chart.encode_block(block)

    def test_nan_price_raises(self):
        block = _bars(2)
        block["open"][1] = float("nan")
        with self.assertRaises(ValueError):
            chart.encode_block(block)

    def test_out_of_range_time_raises(self):
        block = _bars(1)
        block["time"][0] = 5_000_000_000
        with self.assertRaises(ValueError):
            chart.encode_block(block)


class TestCsvHardening(unittest.TestCase):
    def test_narrow_csv_raises_named_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "b.csv"
            p.write_text("date,close\n2024-01-01,1.5\n", encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                chart.bars_from_csv(p)
            self.assertIn("missing columns", str(ctx.exception))
            self.assertIn("high", str(ctx.exception))


class TestIndicatorParsing(unittest.TestCase):
    def test_bare_tokens_default(self):
        inds = chart.parse_indicators("SMA,EMA,BB,RSI")
        self.assertEqual([i["period"] for i in inds], [20, 20, 20, 14])

    def test_ma_alias_and_rsi_macd(self):
        inds = chart.parse_indicators("MA50,RSI14,MACD")
        self.assertEqual([i["type"] for i in inds], ["sma", "rsi", "macd"])
        self.assertEqual(inds[0]["period"], 50)
        self.assertEqual(inds[1]["period"], 14)
        self.assertNotIn("period", inds[2])

    def test_unsupported_raises(self):
        with self.assertRaises(ValueError):
            chart.parse_indicators("ICHIMOKU")
        with self.assertRaises(ValueError):
            chart.parse_indicators("MACD9")


class TestGalleryEscaping(unittest.TestCase):
    def test_gallery_escapes_title(self):
        s = chart.spec("X", {"D1": _bars(2)})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chart.render(s, root / "a.html", title="A")
            idx = chart.gallery(root, title="<b>hi</b>", subtitle="<i>x</i>")
            html = idx.read_text(encoding="utf-8")
            self.assertIn("&lt;b&gt;hi&lt;/b&gt;", html)
            self.assertNotIn("<b>hi</b>", html)


class TestVolumeAndAnnotations(unittest.TestCase):
    def test_volume_round_trips_and_encodes(self):
        b = _bars(3)
        b["volume"] = [10.0, 20.0, 30.0]
        s = chart.spec("X", {"D1": b})
        self.assertEqual(s["timeframes"]["D1"]["volume"], [10.0, 20.0, 30.0])
        self.assertEqual(chart.validate(s), [])
        self.assertEqual(chart.encode_block(s["timeframes"]["D1"])["v"], [10.0, 20.0, 30.0])

    def test_from_csv_annotations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bars.csv").write_text(
                "date,open,high,low,close\n"
                "2024-01-01T00:00:00Z,1,2,0.5,1.5\n"
                "2024-01-02T00:00:00Z,1.5,2.5,1,2\n", encoding="utf-8")
            (root / "zones.json").write_text(
                json.dumps([{"start": 1704067200, "end": 1704153600,
                             "low": 1, "high": 2, "label": "Z"}]), encoding="utf-8")
            (root / "stats.json").write_text(json.dumps({"Sharpe": 1.5}), encoding="utf-8")
            out = root / "page.html"
            rc = chart.main(["from-csv", "--bars", str(root / "bars.csv"),
                             "--zones", str(root / "zones.json"),
                             "--stats", str(root / "stats.json"),
                             "--inds", "SMA2,RSI2,MACD",
                             "--out", str(out)])
            self.assertEqual(rc, 0)
            html = out.read_text(encoding="utf-8")
            self.assertIn('"Z"', html)
            self.assertIn('"RSI2"', html)
            self.assertIn('"MACD"', html)
            self.assertIn('"Sharpe"', html)


class TestViewerAssets(unittest.TestCase):
    def test_viewer_has_volume_and_oscillator_support(self):
        html = (chart.ASSETS / chart.VIEWER_NAME).read_text(encoding="utf-8")
        self.assertIn("addHistogramSeries", html)
        self.assertIn("function rsi(", html)
        self.assertIn("function macd(", html)
        self.assertIn("@media print", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
