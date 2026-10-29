"""Level-line zones (engulfingCore owner, 2026-10-29: "the yellow line showing screenshot entry please make it thinner").

A zone with ``style: "line"`` - or one whose low equals its high - is a single price level: both viewers draw ONE stroke of
the zone's ``width`` (default 1 px in the standard viewer, 0.5 px in the review theme) and no fill. Before this a zero-height
zone was stroked as a rectangle (two edges about a pixel apart), which read as a thick band.
"""
import tempfile
import unittest

from tests.test_viewer import BROWSER, H1, T0, _render, _wide_bars, chart, run_page


class TestLevelLineSpec(unittest.TestCase):
    def test_style_and_width_kept_and_checked(self):
        z = chart.zones_from_rows([{"start": 1, "end": 2, "low": 1.5, "high": 1.5, "style": "line", "width": 0.5}])[0]
        self.assertEqual((z["style"], z["width"]), ("line", 0.5))
        box = chart.zones_from_rows([{"start": 1, "end": 2, "low": 1, "high": 2, "style": "box"}])[0]
        self.assertNotIn("style", box)                          # 'box' is the default and is not written
        self.assertNotIn("width", box)
        for bad in ({"style": "dash"}, {"width": 0}, {"width": 7}, {"width": "x"}):
            with self.assertRaises(ValueError):
                chart.zones_from_rows([dict({"start": 1, "end": 2, "low": 1, "high": 2}, **bad)])

    def test_validate_reports_bad_style_and_width(self):
        s = chart.spec("X", {"H1": _wide_bars(3)})
        s["overlay"]["zones"] = [{"start": 1, "end": 2, "low": 1, "high": 1, "style": "dash"}]
        self.assertTrue(any("zone style" in p for p in chart.validate(s)))
        s["overlay"]["zones"] = [{"start": 1, "end": 2, "low": 1, "high": 1, "width": 9}]
        self.assertTrue(any("zone width" in p for p in chart.validate(s)))
        s["overlay"]["zones"] = [{"start": 1, "end": 2, "low": 1, "high": 1, "style": "line", "width": 0.5}]
        self.assertEqual(chart.validate(s), [])


@unittest.skipUnless(BROWSER, "no headless Edge/Chrome found (set CHARTLAB_BROWSER)")
class TestLevelLinePaint(unittest.TestCase):
    def test_both_viewers_draw_one_stroke(self):
        h1 = _wide_bars()
        zones = [{"start": T0 + 5 * H1, "end": T0 + 40 * H1, "low": 1.1010, "high": 1.1010, "color": "#f2c14e", "label": "Fill"},
                 {"start": T0 + 5 * H1, "end": T0 + 40 * H1, "low": 1.0990, "high": 1.0990, "style": "line", "width": 2},
                 {"start": T0 + 10 * H1, "end": T0 + 30 * H1, "low": 1.0950, "high": 1.1050, "label": "Box"}]
        probe = "return ChartLab.frame().then(function(){return ChartLab.debug().painted.zones})"
        for review, default_w in ((False, 1), (True, 0.5)):
            with self.subTest(review=review), tempfile.TemporaryDirectory() as tmp:
                s = chart.spec("EURUSD", {"H1": h1}, zones=zones, tz="UTC")
                if review:
                    s["reviewPresentation"] = True
                painted = {q["i"]: q for q in run_page(_render(tmp, s), probe)}
                self.assertEqual(set(painted), {0, 1, 2})
                for i, w in ((0, default_w), (1, 2)):
                    self.assertEqual(painted[i].get("style"), "line")
                    self.assertEqual(painted[i]["width"], w)
                    self.assertAlmostEqual(painted[i]["yh"], painted[i]["yl"])
                    self.assertIsNone(painted[i]["fill"])
                self.assertNotIn("style", painted[2])                   # a box stays a box
                self.assertGreater(painted[2]["yl"] - painted[2]["yh"], 10)


if __name__ == "__main__":
    unittest.main()
