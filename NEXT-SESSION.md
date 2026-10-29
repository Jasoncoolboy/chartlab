## v0.4.3 level-line zones (2026-10-29, Claude, branch `level-line-zones-20261029`)

engulfingCore owner: "the yellow line showing screenshot entry please make it thinner". A zone with `style: "line"` (or
low == high) is now ONE stroke of `width` px (default 1; 0.5 in the review theme), no fill; `zones_from_rows` / `validate`
check `style` and `width`. Spec: docs/CHART_SPEC.md zone table; CHANGELOG 0.4.3. Full pytest 198 passed (195 + 3 new in
tests/test_level_lines.py, both viewers painted in headless Edge). Pushed as a feature branch with a PR; main is untouched
until the owner merges. The owner's untracked `agents.md` / `PROJECT_SUMMARY.md` are left alone.

## Review navigation and presentation COMPLETE (2026-10-27)

Owner requested free panning, visible timeframe controls and reusable review presentation. Shared viewer plus opt-in review theme updated. Read docs/REVIEW_NAVIGATION.md. Full pytest: 195 tests and 2 subtests PASS (10 expected synthetic-data warnings). New tests/test_review_navigation.py covers the generic adapter, both free edges, five-digit prices and opt-in theme. Flake8: new test clean; chart.py has 76 pre-existing style findings unchanged from HEAD; no new findings. EngulfingCore real Chromium mouse-input audit passed both directions and right-edge overscroll on7 charts; embedded toolbar and all15 eight-timeframe round trips passed. The first input harness failed to drive mouse events; real CDP input replaced it. File navigation through CDP was aborted by Edge startup; fresh targets over a temporary loopback server worked. Existing file-URL iframe regression also passed. No data/engine changes. Commit/push this completed change.

# ChartLab — next session / handover

## State (2026-10-13, v0.4.2)

- **v0.4.2 (engulfingCore session 29, owner: "the 6 pixel is too close, maybe make it 20px and box shading looks fine
  for now"):** the soft magnet's default reach is 20 px (viewer `MAGNET_PX`, `chart.py` docstring, `docs/CHART_SPEC.md`,
  README). **193 tests pass** (the magnet test checks the 20 px reach).

- **v0.4.1 (engulfingCore session 28, owner):** the crosshair is FREE with a soft magnet — it snaps to the hovered
  candle's high / low / close only within `magnetPx` screen pixels (default 6; spec `magnetPx`, `chart.spec(magnet_px=)`,
  `?magnet=<px>`, 0 = never; a remembered **Magnet** box in the toolbar). lightweight-charts' Magnet mode had pulled it to
  the close at all times. Zone boxes clearer: fill 0.22 (was 0.12; spec `zoneOpacity`, a zone's own `opacity`), border
  0.85, label solid, and `rgb(...)` / `rgba(...)` colours parsed (an rgba colour used to be drawn unchanged for fill,
  border and label alike = the "unclear" boxes on engulfingCore's pages). **193 tests pass** (6 new: the snap through the
  chart's own mouse handler, the zone alphas, the spec checks). Committed and pushed (`418ee2a`,
  2026-10-13, once the owner had signed in to GitHub on this PC).

## State (2026-09-27)

- Only `main` exists, **pushed to `origin` with tags `v0.3.0`** (the 2026-09-20 commit that set 0.3.0 — the tag was
  missing) **and `v0.4.0`** on 2026-09-27. Version string `0.4.0`.
- Tests: **187 pass** (`python -m unittest discover -s tests`, ~40-60 s). `tests/test_viewer.py` runs pages in headless
  Edge (skips without a browser; `CHARTLAB_BROWSER` points at one).
- ✅ **priceData switch done** (priceData `todo.md` project #9, `CLAUDE.md` ✅ block): clean years only by default
  (`--pre-clean` adds only the price-verified FX years, labelled); priceData's manifest verdict read; page data badge
  (spec `notes`); `CostConfig` reads `COST_MODELS`; `backtest --source ftmo`.
- ✅ **Overlays stay put**: zones, trades and drawings are painted in the chart's own frame (series primitive), so a
  zoom, pan, price-axis drag or timeframe switch no longer moves them off their price/time (owner report 2026-09-27).
- ✅ Handover items of 2026-09-26: priceData verification status shown; the 2026-09-21 holes are gone (ChartLab's M1
  check: all 72 native frames of the 9 symbols clean over 2022+, and the 09-14..09-25 window clean on
  EURUSD/GBPUSD/USDJPY; the two real-data `DataWarning`s in the test output are gone); release 0.4.0 cut (local).
- ✅ Known limits closed: page shows its data warnings (badge); `bars="m1"` drops the forming bar; W1/MN on short
  windows are covered by priceData's verdict; a timeframe switch keeps the time window; the legacy backtest ran end to
  end on both sources.

## To do

1. Nothing open in ChartLab itself. **Owner decisions (2026-09-27):** the price axis keeps fitting the candles only — a
   zone or SL/TP outside their range is reached by zooming out, no auto-widening wanted; the Dukascopy path stays as it is.
2. **Optional, in another project:** `scalperEngulfingSystem/tools/chart/system3_setup_pages.py` works around three things
   ChartLab now handles (snapping times to the bar, R written into the label instead of `net`, `page_frames` built from M1).
   It could pass exact times with `net_unit="R"` and use `bars="m1"`. That project's call.
3. Pages built from native EURUSD/GBPUSD/USDJPY M30–D1 between 2026-09-23 and 09-26 should be rebuilt — ChartLab keeps
   none itself (`out/` is scratch); other projects' saved pages are theirs to check (scalperEngulfingSystem's System 3
   pages were built from M1 and are not affected).

## Not verified / known limits

- The Dukascopy datafeed throttled to 30 s–3 min per daily file on 2026-09-27; `download → build → resample → backtest`
  ran end to end on the 15 days that arrived (XAUUSD 2026-05-01..21, in the git-ignored `data/`), not on a long history.
- `tests/test_viewer.py` runs `requestAnimationFrame` on a 16 ms timer: headless Chromium under a virtual-time budget
  stops producing animation frames after load (measured). Real browsers are unaffected; the overlay tests prove the
  painting happens in the chart's frames, under that shim.
- `debug().painted` is the last painted frame: after changing the view, read it after `ChartLab.frame()`.
- The legacy engine books USD with a fixed swap per night: `CostConfig` supports EURUSD, GBPUSD, AUDUSD, NZDUSD, XAUUSD,
  XAGUSD only. It is a demo — never quote it.
- Pre-clean bars (`--pre-clean`) are context only; never test or quote on them.
- Headless Edge `--dump-dom` stalled occasionally until background networking was disabled; `run_page` has a 45 s
  timeout and one retry.
