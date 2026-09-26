# ChartLab — todo

**Status 2026-09-26:** items 1–6 done; open work and caveats are in `NEXT-SESSION.md`.

Issues found generating 80 setup pages for `C:\personalCode\scalperEngulfingSystem` (System 3) on 2026-09-23 via
`setups.setups_from_rows` / `render_pages`. Checked with headless-Edge screenshots before and after each workaround.
The workarounds live in `scalperEngulfingSystem/tools/chart/system3_setup_pages.py`.

## 1. 🔴 An overlay time that is not a bar of the displayed timeframe is silently not drawn

`assets/viewer.html:556` — `timeX(t)` is `chart.timeScale().timeToCoordinate(t)`, which lightweight-charts returns as null for any
time that is not a bar of the series. Then:
- `paintTrades` (~line 614) returns when the entry x is null → **the trade is not drawn at all**;
- `paintZones` (~line 597) returns when BOTH ends are null → a zone that starts before the page window and ends off-grid vanishes.

Repro: a rows file with `entry_time` 08:41 on an M15 page (any M1-precise fill) → the page renders, the Trades drawer lists the
trade, the chart shows nothing. `setups_from_rows` accepts the row without a warning. The same happens on every HIGHER-timeframe
button: an M15-aligned entry has no bar on H1/H4.
- [x] In `timeX`, snap an off-grid time to the bar that CONTAINS it (binary search over the current timeframe's time array:
      the last bar with time ≤ t), so intrabar fills and zones draw on every timeframe button. *(2026-09-26; also: a zone
      spanning the whole page is drawn across it — it vanished when both ends were off the page.)*
- [x] ~~Until then, have `setups_from_rows` warn (or offer `snap=True`)~~ — not needed: the viewer snaps, and the data keeps
      the exact minute (the Trades drawer shows it).
- [x] Test: a row with an off-grid entry and a zone that starts before the window must produce a drawn trade and zone
      (`window.ChartLab.debug()` or a pixel check), on the base timeframe and on a higher `--extra-tfs` button.
      *(`tests/test_viewer.py`, headless Edge; `debug().painted`. With snapping disabled the trade tests fail.)*

## 2. `net` is always rendered as dollars

`assets/viewer.html` (~line 637): `lbl += "  " + (n>=0?"+":"-") + "$" + Math.abs(n).toFixed(0)`. A system that reports R (or pips)
shows "−$1" for −1.1 R, and `toFixed(0)` also rounds away every R-sized number.
- [x] Add a unit to the trade/row (`net_unit`: "$" default, "R", "pips") and format with sensible decimals for non-dollar units.
      *(2026-09-26: spec `netUnit`, rows `net_unit`; R to 0.01, pips to 0.1; chart label and drawer share `fmtNet`.)*

## 3. Native higher-timeframe holes pass straight through to pages

Pages load priceData's native frames (`pricedata.load_frame`). Since priceData's 2026-09-23 refresh, EURUSD/GBPUSD/USDJPY
M30/H1/H4 miss up to 82 bars from 2026-09-21 (`priceData/todo.md` item 1). A page over that window shows a gap, and an overlay
whose bar is missing is not drawn (item 1). 14 of 20 EURUSD H1 System 3 pages overlapped it.
- [x] Optionally build a page's timeframes from M1 (aggregate on the source clock, then convert — the approach
      `system3_setup_pages.page_frames` uses), or warn when a frame has fewer bars than its M1 aggregate in the page window.
      *(2026-09-26: both. `bars="m1"` / `--bars m1`; and by default `build_spec` / `setup` / `rows` compare native bars with
      M1 over the page window (`pricedata.check_vs_m1`) and warn, naming the bars and the pages affected. Matches priceData's
      `verify_htf_vs_m1.py` count for count on the 2026-09-21 holes.)*
- [ ] Surface priceData's verification status once its manifest records it (`priceData/todo.md` item 2).
      **Blocked:** the manifest has `grid_verified` / `fidelity_verified` only, no HTF-vs-M1 verdict yet. ChartLab's own
      M1 check covers the need meanwhile.

## 4. Opens fully zoomed out (known, pre-existing — observed on all 80 pages)

A setup window of ~105 bars opens with empty space to its left, and a trade that lasts one or two bars (most System 3 trades)
is a few pixels wide at the default zoom.
- [x] Open a setup page zoomed to its own window (the `pre`/`post` bars), e.g. an initial visible range in the spec.
      *(2026-09-26. Root cause: the initial fit only ran when bars were off screen, which a ~105-bar window never is at 7 px
      per bar, so it never fitted. Fixed, plus `lockVisibleTimeRangeOnResize`, plus spec `view` {from, to}; setup pages set it.)*

## 5. Zone labels overflow the box

`paintZones` draws the label whenever the box is > 90 px wide, but does not clip or truncate it: a long label runs past the
box's right edge (seen on XAUUSD H1 pages).
- [x] Clip the label to the box width (or truncate with an ellipsis and show the full text in a tooltip).
      *(2026-09-26: ellipsis; full text in the status bar when the cursor is inside the zone.)*

## 6. Legacy engine: swap timing is not FTMO's (found 2026-09-23 by testEGEA while updating the cost defaults)

`engine.py::check_swap` charges swap at 00:00 of the DATA clock (UTC on Dukascopy frames) and triples it when that midnight
is a Wednesday (`weekday_a[i] == 2`, i.e. the Tue→Wed night). FTMO rolls over at 00:00 SERVER time (21:00/22:00 UTC) and
charges x3 for the Wednesday→Thursday night (tester-verified: EURUSD −10.33 → −30.99, XAUUSD −83 → −249 USD/lot). The cost
DEFAULTS are now the measured FTMO values (CHANGELOG, Unreleased), so only the timing is wrong.
- [x] Charge at 00:00 server (convert with `sources.utc_to_ftmo_server`) and triple the Wed→Thu rollover; test both clocks.
      *(2026-09-26: a rollover is a change of server day between two bars — gold has no bar at 00:00 server, so the old
      "bar at midnight" test never charged it on FTMO data. Weekdays one night, Wednesday three, weekends none; charged
      before the bar's orders. `BacktestConfig.data_clock`. `tests/test_engine.py`: the old engine fails 8 of 11.)*
- [x] Note for users of saved cost files: a JSON without `commission_pct_side` now gets the 0.0007 % default ADDED to its flat
      `commission_per_side_per_lot` (FX files should set `commission_pct_side: 0` and `commission_per_side_per_lot: 2.5`).
      *(CHANGELOG + README, and `CostConfig.from_json` warns when it happens.)*
