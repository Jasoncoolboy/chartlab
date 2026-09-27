# ChartLab — next session / handover

## State (2026-09-27)

- Only `main` exists. It is **ahead of `origin/main` and not pushed**; tags `v0.3.0` (the 2026-09-20
  commit that set 0.3.0 — the tag was missing) and `v0.4.0` are local only. Version string `0.4.0`.
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

1. **Push when you want it published:** `git push origin main --tags` (sends `main`, `v0.3.0`, `v0.4.0`).
2. **Idea, not done — the price axis ignores overlays.** Autoscale fits the candles only, so a setup page whose zone or
   SL/TP lies outside the candles' range hides it until the price axis is dragged. The overlay primitive could return
   `autoscaleInfo()` (the zones/trades inside the visible time range) so they are always in view. Decide whether a page
   should widen its axis for them.
3. **Optional, in another project:** `scalperEngulfingSystem/tools/chart/system3_setup_pages.py` works around three things
   ChartLab now handles (snapping times to the bar, R written into the label instead of `net`, `page_frames` built from M1).
   It could pass exact times with `net_unit="R"` and use `bars="m1"`. That project's call.
4. Pages built from native EURUSD/GBPUSD/USDJPY M30–D1 between 2026-09-23 and 09-26 should be rebuilt — ChartLab keeps
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
