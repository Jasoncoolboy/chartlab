"""Tests that run the viewer itself, in a headless Edge/Chrome.

Run from the repository root::

    python -m unittest tests.test_viewer -v

Each test renders a page, loads it headless with a script that drives
``window.ChartLab`` (``setTF``, ``debug``) and reads back what the viewer
actually PAINTED - a page that renders but draws nothing is the failure these
catch. Skipped when no Chromium browser is found (set ``CHARTLAB_BROWSER`` to
its executable to point at one). The setup-page tests also need pandas.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import chart  # noqa: E402

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None


def _find_browser() -> str | None:
    env = os.environ.get("CHARTLAB_BROWSER")
    candidates = [env] if env else []
    candidates += [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        shutil.which("msedge"), shutil.which("google-chrome"), shutil.which("chromium"),
        shutil.which("chrome"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


BROWSER = _find_browser()
_RESULT = re.compile(r'<pre id="__chartlab_test">(.*?)</pre>', re.S)


def run_page(page: Path, body: str, *, delay_ms: int = 1500, size=(1400, 800)) -> dict:
    """Load ``page`` headless and return what the JS function ``body`` returns.

    ``body`` is the body of a function run ``delay_ms`` (virtual time) after the
    page loaded; it can call ``ChartLab.setTF``/``ChartLab.debug`` and must return
    something JSON-serializable.
    """
    html = page.read_text(encoding="utf-8")
    probe = ("<script>window.addEventListener('load',function(){setTimeout(function(){"
             "var out;try{out=(function(){" + body + "})()}catch(e){out={error:String(e&&e.stack||e)}}"
             "var d=document.createElement('pre');d.id='__chartlab_test';d.textContent=JSON.stringify(out);"
             "document.body.appendChild(d)}," + str(delay_ms) + ")})</script></body>")
    # The probed copy sits beside the page so a relative lib/ still resolves.
    probed = page.with_name("__probe_" + page.name)
    probed.write_text(html.replace("</body>", probe), encoding="utf-8")
    try:
        for attempt in (1, 2):      # headless --dump-dom occasionally stalls; one retry, never a hang
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile:
                cmd = [BROWSER, "--headless=new", "--disable-gpu", "--no-first-run",
                       "--no-default-browser-check", "--disable-extensions", "--disable-sync",
                       "--disable-background-networking", "--disable-component-update",
                       f"--user-data-dir={profile}", f"--window-size={size[0]},{size[1]}",
                       f"--virtual-time-budget={delay_ms + 4000}", "--dump-dom", probed.as_uri()]
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                       timeout=45)
                    break
                except subprocess.TimeoutExpired:
                    if attempt == 2:
                        raise
    finally:
        probed.unlink(missing_ok=True)
    m = _RESULT.search(r.stdout)
    if not m:
        raise AssertionError(f"no result from the page (rc {r.returncode}): {r.stderr[-400:]}")
    import html as _h
    out = json.loads(_h.unescape(m.group(1)))
    if isinstance(out, dict) and out.get("error"):
        raise AssertionError("page script failed: " + out["error"])
    return out


M15 = 900
T0 = 1_700_000_000 - (1_700_000_000 % 3600)          # an hour boundary, UTC


def _walk(n, step, start=T0, base=1.1000):
    """A 5-digit FX-like bar block that rises 1 pip per bar."""
    return {"time": [start + i * step for i in range(n)],
            "open": [round(base + i * 1e-4, 5) for i in range(n)],
            "high": [round(base + i * 1e-4 + 6e-4, 5) for i in range(n)],
            "low": [round(base + i * 1e-4 - 6e-4, 5) for i in range(n)],
            "close": [round(base + i * 1e-4 + 2e-4, 5) for i in range(n)]}


def _package():
    """The repository root as the ``chartlab`` package (as run.py does)."""
    import importlib
    import importlib.util
    module = sys.modules.get("chartlab")
    if module is None or Path(module.__file__).resolve().parent != ROOT:
        spec = importlib.util.spec_from_file_location(
            "chartlab", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
        module = importlib.util.module_from_spec(spec)
        sys.modules["chartlab"] = module
        spec.loader.exec_module(module)
    importlib.import_module("chartlab.setups")
    return module


def _render(tmp, s, name="p.html"):
    return chart.render(s, Path(tmp) / name, inline_lib=True)


@unittest.skipUnless(BROWSER, "no headless Edge/Chrome found (set CHARTLAB_BROWSER)")
class TestOffGridOverlays(unittest.TestCase):
    """todo item 1: an overlay time that is not a bar of the shown timeframe must still be drawn."""

    def test_off_grid_trade_and_zone_are_drawn_on_every_timeframe(self):
        m15 = _walk(104, M15)
        h1 = _walk(26, 3600)
        entry = T0 + 50 * M15 + 11 * 60                   # 12:41-style: inside bar 50, not on the grid
        exit_ = T0 + 53 * M15 + 7 * 60
        s = chart.spec("EURUSD", {"M15": m15, "H1": h1}, default_tf="M15",
                       trades=[{"dir": "long", "entryTime": entry, "entryPrice": 1.1052,
                                "exitTime": exit_, "exitPrice": 1.1056}],
                       zones=[{"start": T0 - 86400, "end": T0 + 40 * M15 + 300,
                               "low": 1.1010, "high": 1.1030, "label": "Z"}])
        with tempfile.TemporaryDirectory() as tmp:
            out = run_page(_render(tmp, s), """
                var r={m15:ChartLab.debug()};
                ChartLab.setTF('H1');r.h1=ChartLab.debug();
                document.getElementById('btnTrades').click();
                r.row=document.querySelector('#drawerBody .tm').textContent;
                return r;""")
        for tf in ("m15", "h1"):
            d = out[tf]
            self.assertEqual(len(d["painted"]["trades"]), 1, f"{tf}: trade not drawn")
            self.assertEqual(len(d["painted"]["zones"]), 1, f"{tf}: zone not drawn")
            tr = d["painted"]["trades"][0]
            self.assertIsNotNone(tr["xb"], f"{tf}: exit not placed")
            self.assertLess(tr["xa"], tr["xb"])
            self.assertTrue(d["firstX"] <= tr["xa"] <= d["lastX"])
        # M15: the entry sits on the bar that contains it (bar 50), not on a neighbour.
        d = out["m15"]
        bar_w = (d["lastX"] - d["firstX"]) / 103
        self.assertAlmostEqual(out["m15"]["painted"]["trades"][0]["xa"], d["firstX"] + 50 * bar_w, delta=1.0)
        # The data is not rewritten: the drawer still shows the exact minute (MYT = UTC+8).
        self.assertEqual(out["row"], chart.to_iso(entry, "MYT"))

    def test_zone_edges_off_the_page(self):
        m15 = _walk(40, M15)
        last = T0 + 39 * M15
        zones = [
            {"start": T0 - 86400, "end": last + 86400, "low": 1.1005, "high": 1.1010},      # spans the page
            {"start": T0 - 86400, "end": T0 - 3600, "low": 1.1005, "high": 1.1010},         # before it
            {"start": last + 2 * M15, "end": last + 86400, "low": 1.1005, "high": 1.1010},  # after it
            {"start": last + 60, "end": last + 86400, "low": 1.1005, "high": 1.1010},       # starts in the last bar
        ]
        trades = [{"dir": "long", "entryTime": last + M15 + 60, "entryPrice": 1.104}]       # after the last bar
        s = chart.spec("EURUSD", {"M15": m15}, zones=zones, trades=trades)
        with tempfile.TemporaryDirectory() as tmp:
            d = run_page(_render(tmp, s), "return ChartLab.debug();")
        drawn = sorted(z["i"] for z in d["painted"]["zones"])
        self.assertEqual(drawn, [0, 3])
        self.assertEqual(d["painted"]["trades"], [])


@unittest.skipUnless(BROWSER, "no headless Edge/Chrome found (set CHARTLAB_BROWSER)")
class TestOpeningView(unittest.TestCase):
    """todo item 4: a short window must fill the chart; a spec ``view`` is where the page opens."""

    def test_short_window_fills_the_width(self):
        s = chart.spec("EURUSD", {"M15": _walk(105, M15)})
        with tempfile.TemporaryDirectory() as tmp:
            d = run_page(_render(tmp, s), "return ChartLab.debug();")
        span = d["lastX"] - d["firstX"]
        self.assertGreater(span, 0.9 * d["width"], f"bars span {span:.0f}px of {d['width']:.0f}px")

    def test_page_opens_on_the_spec_view_in_either_display_zone(self):
        bars = _walk(400, M15)
        lo, hi = bars["time"][200], bars["time"][260]
        s = chart.spec("EURUSD", {"M15": bars}, view={"from": lo, "to": hi})
        with tempfile.TemporaryDirectory() as tmp:
            page = _render(tmp, s)
            d = run_page(page, "return ChartLab.debug();")          # opens in MYT
            s["tz"] = "UTC"
            du = run_page(_render(tmp, s, "u.html"), "return ChartLab.debug();")
        self.assertEqual(d["tz"], "MYT")
        self.assertEqual(d["visible"], {"from": lo + 28800, "to": hi + 28800})
        self.assertEqual(du["visible"], {"from": lo, "to": hi})


@unittest.skipUnless(BROWSER, "no headless Edge/Chrome found (set CHARTLAB_BROWSER)")
class TestZoneLabels(unittest.TestCase):
    """todo item 5: a zone label never runs past its box; the full text is in the status bar."""

    def test_long_label_is_cut_to_the_box_and_short_one_is_whole(self):
        m15 = _walk(105, M15)
        long_label = "EF zone H1 2024-03-06 strength 0.83 retest #2 long label that cannot fit"
        zones = [{"start": T0 + 10 * M15, "end": T0 + 22 * M15, "low": 1.1000, "high": 1.1030,
                  "label": long_label},
                 {"start": T0 + 40 * M15, "end": T0 + 90 * M15, "low": 1.1000, "high": 1.1030,
                  "label": "EG"}]
        s = chart.spec("EURUSD", {"M15": m15}, zones=zones, tz="UTC")
        with tempfile.TemporaryDirectory() as tmp:
            out = run_page(_render(tmp, s), """
                return {d:ChartLab.debug(),at:ChartLab.zonesAt(%d,1.1015),off:ChartLab.zonesAt(%d,1.1015)};"""
                           % (T0 + 15 * M15, T0 + 30 * M15))
        z0, z1 = sorted(out["d"]["painted"]["zones"], key=lambda z: z["i"])
        self.assertTrue(z0["label"].endswith("…"), z0.get("label"))
        self.assertTrue(long_label.startswith(z0["label"][:-1]))
        self.assertLessEqual(z0["labelX"] + z0["labelW"], z0["xb"])       # inside the box
        self.assertEqual(z1["label"], "EG")
        self.assertEqual(out["at"], [long_label])                          # full text under the cursor
        self.assertEqual(out["off"], [])


@unittest.skipUnless(BROWSER, "no headless Edge/Chrome found (set CHARTLAB_BROWSER)")
class TestNetUnitShown(unittest.TestCase):
    """todo item 2: -1.07 R must read "-1.07R", not "-$1"."""

    def test_label_and_drawer_use_the_unit(self):
        m15 = _walk(60, M15)
        mk = lambda i, net, unit=None: dict({"dir": "long", "entryTime": T0 + i * M15, "entryPrice": 1.102,
                                             "exitTime": T0 + (i + 2) * M15, "exitPrice": 1.1025,
                                             "net": net}, **({"netUnit": unit} if unit else {}))
        trades = [mk(5, -1.07, "R"), mk(20, 12.34, "pips"), mk(35, -250.4)]
        s = chart.spec("EURUSD", {"M15": m15}, trades=trades)
        with tempfile.TemporaryDirectory() as tmp:
            out = run_page(_render(tmp, s), """
                var d=ChartLab.debug();document.getElementById('btnTrades').click();
                var nts=[].map.call(document.querySelectorAll('#drawerBody .nt'),function(e){return e.textContent});
                return {labels:d.painted.trades.map(function(t){return t.label}),drawer:nts};""")
        self.assertEqual(out["drawer"], ["−1.07R", "+12.3 pips", "−$250"])
        for label, tail in zip(out["labels"], out["drawer"]):
            self.assertTrue(label.endswith(tail), label)


@unittest.skipUnless(BROWSER and pd is not None, "needs a headless browser and pandas")
class TestSetupPagesOffGrid(unittest.TestCase):
    """todo item 1 through the real rows path: setups_from_rows -> render_pages with --extra-tfs."""

    def test_rows_with_m1_precise_times_draw_on_base_and_extra_tf(self):
        setups = _package().setups
        idx = pd.date_range("2024-03-04 00:00", periods=400, freq="15min")
        base = 1.1 + pd.Series(range(400), index=idx) * 1e-5
        m15 = pd.DataFrame({"Open": base, "High": base + 3e-4, "Low": base - 3e-4, "Close": base + 1e-4})
        h1 = m15.resample("1h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
        rows = [{"dir": "long", "arm": "2024-03-06 10:30", "entry_time": "2024-03-06 10:41",
                 "entry_price": 1.1020, "stop": 1.1000, "target": 1.1060,
                 "exit_time": "2024-03-06 12:07", "exit_price": 1.1060, "id": "offgrid",
                 "zone": {"start": "2024-03-01 00:00", "end": "2024-03-06 09:52", "low": 1.1010,
                          "high": 1.1025, "label": "EF zone"}}]
        found = setups.setups_from_rows(rows, "M15", clock="utc", symbol="EURUSD")
        with tempfile.TemporaryDirectory() as tmp:
            pages = setups.render_pages(m15, found, Path(tmp), lib_dir="lib",
                                        extra_frames={"H1": h1}, tz="UTC")
            out = run_page(pages[0], """
                var r={base:ChartLab.debug()};ChartLab.setTF('H1');r.h1=ChartLab.debug();return r;""")
        for tf in ("base", "h1"):
            self.assertEqual(len(out[tf]["painted"]["trades"]), 1, f"{tf}: trade not drawn")
            self.assertEqual(len(out[tf]["painted"]["zones"]), 1, f"{tf}: zone not drawn")


if __name__ == "__main__":
    unittest.main()
