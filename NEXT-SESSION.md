# ChartLab — next session / handover

## State (2026-09-26)

- `main` = `origin/main`, no other branches. Version string still `0.3.0`; everything since is under
  **Unreleased** in `CHANGELOG.md`.
- `todo.md` items 1–6 (from the 2026-09-23 System 3 pages) are **done**: off-grid overlays drawn at their containing
  bar, pages open on their window (`view`), `netUnit` ($ / R / pips), zone labels cut to the box, pages from M1
  (`--bars m1`) with native-vs-M1 checking on by default, and the legacy engine's swap at 00:00 FTMO server
  (Wed→Thu ×3).
- Tests: **166 pass** (`python -m unittest discover -s tests`, ~40 s). `tests/test_viewer.py` runs pages in headless
  Edge (skips without a browser; `CHARTLAB_BROWSER` points at one).

## To do

1. **Blocked on priceData — show its verification status** (`todo.md` item 3, second bullet). Its manifest records only
   `grid_verified` / `fidelity_verified`, not the HTF-vs-M1 verdict (`priceData/todo.md` item 2). When it does, read it in
   `pricedata.load_frame` / `build_spec` next to ChartLab's own `check_vs_m1`.
2. **After priceData repairs the 2026-09-21 holes** (`priceData/todo.md` item 1):
   - `pricedata.check_vs_m1` over `start="2026-09-14"` for EURUSD / GBPUSD / USDJPY should come back clean, and the two
     real-data `DataWarning`s in the test output (from `test_digits_per_instrument`) should disappear;
   - any page built from native EURUSD/GBPUSD/USDJPY M30–D1 covering 2026-09-21 or later should be rebuilt (or use `--bars m1`).
3. **Release 0.4.0** when you want one: move `CHANGELOG.md` Unreleased under a version, bump `__init__.__version__`,
   tag, push.
4. **Optional, in another project:** `scalperEngulfingSystem/tools/chart/system3_setup_pages.py` works around three things
   ChartLab now handles (snapping times to the bar, R written into the label instead of `net`, `page_frames` built from M1).
   It could pass exact times with `net_unit="R"` and use `bars="m1"`. That's that project's call, not required.

## Not verified / known limits

- The legacy `backtest` command has never run end-to-end here (no Dukascopy parquet under `data/parquet/`); the engine's
  swap timing is covered only by `tests/test_engine.py` (synthetic gold-session bars, both clocks).
- Data warnings go to the terminal only; a page does not show that its bars disagree with M1 (a footer badge would).
- With `bars="m1"` the newest bin can be partial when M1 ends inside it (native files skip forming bars).
- W1/MN on short page windows report "NOT verified against M1" — expected: M1 covers no whole week/month there.
- Not checked: whether switching timeframe keeps the same time window on screen (the viewer was not changed there).
- Headless Edge `--dump-dom` stalled occasionally until background networking was disabled; `run_page` now has a 45 s
  timeout and one retry.
