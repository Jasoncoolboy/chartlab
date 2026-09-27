# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### priceData is the one source of FTMO bars and costs (owner rule 2026-09-26; switched 2026-09-27)

- **Clean years only.** Pages use priceData's `data/clean` (its verified-clean years, from
  `clean_from` = 2022-01-01); a `start` before it raises a `DataWarning` saying what was left out.
  `pre_clean=True` / `--pre-clean` (`load_frame`, `build_spec`, `chart`, `setup`, `rows`) adds, for
  context only, the earlier years whose *prices* priceData verified (`pricedata.pre_clean_from`: FX
  from 2020-01-01, GBPUSD from 2019-01-02; none for XAUUSD/XAGUSD/BTCUSD). The repaired years before
  them are never loaded, and the page labels pre-clean bars. Checked on the real data: those years
  match M1 on M5…MN (48 frames), and priceData's M1 detectors find no day or hour aggregate there
  (positive control: 256 / 3,826 on the raw 2019 / 2021 dumps).
- **priceData's own verdict is read** from its manifest (`pricedata.verification_status`,
  `describe_verification`): a failed or missing `htf_verified` raises a `DataWarning`. A short window
  that M1 cannot check on W1/MN is covered by that verdict (`covered_by_pricedata`), so it no longer
  reads "NOT verified"; a missing M1 file still does.
- **Data notes on the page** — spec field `notes` (`chart.notes_from_rows`; text or `{level: info|warn,
  text}`), a footer badge (`✓ Data` / amber `⚠ Data: N warnings`) that lists them when clicked.
  `build_spec` writes them (`pricedata.page_notes`); `setup` / `rows` write each page's own (its bars'
  M1 check, a pre-clean label only where the page shows pre-clean bars); Dukascopy pages say they are
  not FTMO data, and FTMO bars read from a file say they are not priceData's. Closes the known limit
  "data warnings go to the terminal only".
- **Costs from `COST_MODELS`.** `CostConfig` fields left at `None` are filled from priceData's
  `COST_MODELS` for its `symbol` (`config.ftmo_costs`, `pricedata.cost_model`, `pricedata.package`):
  no copied cost number is left in ChartLab. The XAUUSD values are unchanged (0.0007 %/side, $0.05
  slippage, swap −83 / −8.3). FX symbols get their flat $/lot commission; non-USD-quoted symbols and
  BTCUSD's percent-of-price swap are refused (the engine books USD with a fixed swap per night).
- **`backtest --source ftmo --symbol …`**: the legacy demo runs on priceData's clean years (ask = BID +
  priceData's spread) and prints the costs it used. It had never run end to end before; it now does
  (XAUUSD 2022-01 → 2026-09 in ~20 s; commission and swap totals reconcile by hand). Never quote it.
- `rows` names `--pre-clean` when a row falls before the clean data.

### Fixed (2026-09-27)
- **Zones, trades and drawings drifted off their price and time on zoom / pan** (reported on generated
  setup pages: "the drawn zone moved away and came back only after resetting the drawings"). They were
  painted on a canvas over the chart, repainted on the time-range event — which fires before the price
  axis re-scales — and never on a price-axis drag or zoom, which fires no event. They are now painted
  inside the chart's own render pass (a series primitive, lightweight-charts 4.2 `attachPrimitive`),
  with the scales of the frame being drawn, so they cannot drift. Measured on one probe: the old viewer
  was 338 px off after a pan/zoom, 126 px after a price-axis change and 2,190 px after a timeframe
  switch; the new one 0 px (`tests/test_viewer.py` `TestOverlaysStayPut`, which also draws a rectangle
  with the Rectangle tool). The `#overlay` canvas is now only the drawing tools' input layer.
- **A timeframe switch kept the bar numbers, not the time window** (bars 40–160 of M15 became bars
  40–160 of H1, days later). It now opens on the bars that contain the old window, at least 20 wide.
- **`bars="m1"` no longer shows the bar still forming** when M1 ends inside it: bins that end after
  `pricedata.complete_until` (the latest end of any native bar — priceData's dumper writes only complete
  bars — or the minute after the last M1 bar) are dropped. On the real data the newest M1-built bar now
  equals the native newest bar on all 9 symbols × M5…MN; a native file with a hole at its tail does not
  pull the cut-off back.

### Added (2026-09-27)
- `window.ChartLab.frame()` (a Promise resolved after the chart has painted the overlay again),
  `ChartLab.api()` (`{chart, series}`), and `debug().now` (where each zone / trade / drawing belongs
  under the current scales, to compare with `painted`, which now also lists `drawings`).
- `tests/test_viewer.py` runs `requestAnimationFrame` on a 16 ms timer: headless Chromium under a
  virtual-time budget stops producing animation frames after load (measured), and the chart paints in them.

Fixes for the issues found generating 80 System 3 setup pages on 2026-09-23 (`todo.md`).

### Fixed
- **Overlays off the timeframe's grid were silently not drawn.** A trade or zone time that is not a
  bar of the timeframe on screen (an M1-precise fill on an M15 page, an M15 entry on the H1 button)
  is now drawn at the bar that contains it; the Trades drawer keeps the exact minute. A zone that
  starts before the page and ends after it is drawn across it (it vanished), and an open trade whose
  entry has scrolled off the left keeps its line.
- **Short windows opened squeezed into the right of the chart** ("opens fully zoomed out"): the
  initial fit only ran when bars were off screen, which a ~105-bar window never is at the default
  7 px per bar. The page now re-applies its opening view until the chart's width settles and keeps
  the time range on resize.
- **`net` was always printed as dollars** (−1.07 R read `−$1`). See `netUnit` below.
- **Zone labels ran past their box.** They are cut to the box with `…`; the full label shows in the
  status bar when the cursor is inside the zone.
- **Legacy engine swap timing.** Swap was charged only on a bar stamped exactly 00:00 of the data
  clock (UTC on Dukascopy frames), tripled on the Tue→Wed night, and never at all on FTMO gold
  (no bar at 00:00 server: break 23:50–01:05). It now rolls over at **00:00 FTMO server time** —
  one night per weekday that ends with the position open, ×3 for Wednesday's (Wed→Thu, tester-verified
  XAUUSD −83 → −249), none at the weekend — and is charged before the bar's orders, so a position
  closed at the first bar after the rollover pays it and one opened there does not.
  `BacktestConfig.data_clock` (`"utc"` default, `"ftmo"`) names the bars' clock.

### Added
- Spec field **`view`** `{from, to}` (UTC): the time range a page opens on (`chart.norm_view`,
  `spec(view=)`, `pricedata.build_spec(view=, clock=)`). Setup pages open on their pre/post window.
- Trade field **`netUnit`**: `"$"` (default), `"R"` (`−1.07R`) or `"pips"` (`+12.3 pips`); rows take
  `net_unit`. Unknown units are refused (`chart.norm_net_unit`).
- **Pages built from M1** — `pricedata.load_frame(..., bars="m1")`, `build_spec(bars="m1")`,
  CLI `--bars m1` on `chart` / `setup` / `rows`: every timeframe aggregated from priceData M1 on the
  FTMO server clock (MT5's grid, priceData's own rules), then converted to UTC.
- **Native bars checked against M1 by default.** `pricedata.check_vs_m1` reports missing, mismatched
  and orphan bars against the M1 build (no M1 file = "NOT verified", never clean); `build_spec` raises
  a `pricedata.DataWarning`, and `setup` / `rows` print it over the bars the pages show with the number
  of pages affected. `verify_m1=False` / `--no-m1-check` turns it off. On real data it reproduces
  priceData's `verify_htf_vs_m1.py` count for count (EURUSD M30 65 / H1 32 / H4 8 missing from
  2026-09-21, GBPUSD 82 / 41 / 10, USDJPY H1 41 / H4 10, a wrong D1 bar on all three) and finds 2025
  clean on every timeframe.
- `setups.page_windows`: the bars each setup page shows per timeframe (same cut as `slice_spec`).
- `window.ChartLab.debug()` reports `painted` (the trades and zones actually drawn, with x positions),
  `view`, `width`; `window.ChartLab.setTF(tf)`.
- `tests/test_viewer.py` loads pages in headless Edge/Chrome and checks what is drawn (skipped without a
  browser; `CHARTLAB_BROWSER`); `tests/test_engine.py` covers swap timing in both clocks.

### Changed
- Legacy backtest cost defaults (`CostConfig`) are now FTMO's measured XAUUSD costs: commission 0.0007 % of notional
  per side (new field `commission_pct_side`, added to the flat `commission_per_side_per_lot`), slippage $0.05 per fill,
  swap -83 / -8.3 USD per lot per night. Source: FTMO account probe 2026-09-18 and MT5 tester cost probe 2026-09-23
  (`testEGEA/results/mt5/egbook_costprobe.csv`). The engine is still a demo.
- ⚠ A cost file saved before `commission_pct_side` existed gets the 0.0007 % default **added** to its flat
  commission; `CostConfig.from_json` now warns. FX files should set `commission_pct_side: 0` and
  `commission_per_side_per_lot: 2.5`.

## [0.3.0] - 2026-09-20

Usable from other systems on FTMO or Dukascopy data: UTC inside, Malaysian time on screen,
and 5-digit FX fixed.

### Added

- **Two sources, identified and converted to UTC** (`sources.py`). `ftmo` (priceData) labels are
  FTMO server time - UTC+2, UTC+3 during US DST - and are converted to true UTC; `dukascopy`
  is already UTC. `detect` / `resolve` identify a frame from its tag, column shape, then a
  weekend fingerprint, print the evidence, and refuse to guess (`--source auto`, `--assume`).
  The rule was verified against real Dukascopy H4 at the same UTC instant (XAUUSD 0.79 % of the
  bar range over 9,905 buckets; in the US/EU-DST-disagreement weeks 0.69 % vs 20.0 % for the EU
  rule on GBPUSD) and against every D1 bar of all 9 symbols opening at 17:00 New York.
- **Malaysian time as a display overlay.** The page opens in MYT (UTC+8) with MYT | UTC buttons;
  `?tz=UTC`, the spec's `tz` field and a remembered choice pick the zone. The data is never
  shifted. New spec fields `tz` and `source`; `chart.to_iso(seconds, tz, suffix)`.
- `clock` (`ftmo` | `utc` | `myt`) is **required** wherever times are handed in with a source's
  bars - `pricedata.build_spec` trades/zones/equity, `setups_from_rows`, `--clock` - because a
  wrong guess is a silent 2-3 hour error. Zone-carrying times (`...Z`, `+08:00`) are absolute.
- `pricedata.py`: strict loader + `build_spec` for the clean bars in `C:\personalCode\priceData`
  (native timeframes, `PRICEDATA_ROOT` / `root=`). Refuses unsorted, duplicated, NaN,
  incoherent-OHLC or timezone-aware files, and re-checks after the UTC conversion.
- `precision` spec field (0-8), inferred from the bars (`chart.infer_precision`) and used
  for the price axis, legends, trade labels and indicator series.
- `setups.setups_from_rows` + `python run.py rows`: setup pages from another system's rows,
  with wrong-side stop/target, missing fields/clock, duplicate ids and out-of-range triggers
  refused by name. `--extra-tfs` adds timeframe buttons cut to each setup's window.
- `chart`/`setup`/`rows` accept `--source ftmo|dukascopy|auto`, `--symbol`, `--root`, `--tz`,
  `--extra-tfs`; `chart` adds `--max-bars`; `setup`/`rows` accept an absolute `--out`.
- `export.ohlc_columns` / `export.to_bid_frame`: priceData (`Open/High/...`), lowercase and
  `bid_*` frames all work. 80 new tests (130 total), including bar-for-bar checks against the
  real priceData parquet and the FTMO->UTC rule against the real Dukascopy library.

### Changed

- **All times in a spec are true UTC.** (Not yet released, so nothing to migrate.) Bars from
  priceData are converted; setup ids built by the finders carry UTC stamps; CLI `--start/--end`
  are UTC.
- Compact encoding stores prices at `10**precision` with the scale in the block (`s`)
  instead of a fixed `1e4`; lossless for FX, and BTC no longer overflows above 214,748.
  Overflow raises `ValueError` instead of a bare `struct.error`. Pages from 0.2.0 still decode.
- `setups` no longer hard-codes `XAUUSD` or 0.5 lots; the symbol comes from the setup or
  `--symbol`, and lots are shown only when given. Catalog and page titles show times in the
  display zone and escape labels.
- `backtest` prints a warning: legacy Dukascopy XAUUSD only, costs are not the measured FTMO costs.
- `--source pricedata` is now `--source ftmo` (the old name still works).

### Fixed

- **5-digit FX was unreadable and distorted.** The viewer showed 2-3 decimals on the axis and
  legends (`1.146`), and the exporter and compact encoder rounded to 4 dp - one whole pip - which
  altered 45% of EURUSD M15 candle bodies by more than 20%. Setup finders rounded entry, stop,
  target and zone edges to 4 dp the same way.
- `export.bars_block` raised `KeyError: 'open'` on priceData frames.
- Docs: removed "the viewer has no volume pane" and "round prices to 4 decimals", the reference
  to a non-existent `configs/`, and `python3` spellings that do not work on Windows.

## [0.2.0] - 2026-09-18

Hardening pass plus optional volume and oscillator indicators.

### Added

- Volume histogram in the viewer, shown only when a timeframe carries a
  `volume` series; `chart --volume` includes it from the pipeline.
- RSI and MACD indicator types, computed by the viewer on their own price
  scale; `parse_indicators` accepts `RSI14` and `MACD`.
- `from-csv` accepts `--zones`, `--stats` and `--inds` so the standalone CLI
  can build fully annotated pages.
- Print stylesheet that hides the toolbar, footer and drawer.

### Changed

- `validate` now rejects null/NaN OHLC, duplicate timestamps, and equity or
  indicator length mismatches instead of letting the viewer fail.
- `encode_block` raises clear errors for null/NaN prices and out-of-range
  timestamps.
- `parse_indicators` no longer crashes on bare `SMA`/`EMA`, maps `MA50` to
  `SMA(50)`, and reports unsupported names.
- `bars_from_csv` names missing columns instead of raising `IndexError`.
- `gallery` escapes its title/subtitle; the viewer escapes legend text.
- Viewer color helper passes non-hex colors through instead of producing
  invalid `rgba(NaN,...)`.

## [0.1.0] - 2026-09-16

Initial public release: a lean, dependency-light toolkit for turning real
market bars and backtest output into self-contained, offline HTML charts.

### Added

- `chart.py` - a single-file, standard-library-only spec-to-HTML renderer plus
  `render`, `validate`, `from-csv` and `loader` subcommands. The JSON spec is
  the stable integration contract.
- Backtest engine (`engine.py`, `strategies.py`, `metrics.py`) driven by
  precomputed indicators and cost models, with MT5-compatible semantics
  (closed-bar signals, next-bar fills, intrabar SL/TP, daily swap and
  mark-to-market equity).
- `setups.py` catalog/multi-page rendering that shares a single vendored
  `lib/` directory across nested output folders.
- `chart.normalize()` - coerces raw specs (ISO timestamps, compact OHLC keys,
  trade/zone aliases) into canonical form; applied automatically by
  `render`, `validate` and the CLI.
- Stats panel: `chart.stats_from_rows()` and `export.stats_block()` render a
  tone-aware metrics panel (list, mapping or tone-alias inputs).
- Trades drawer: lazily renders large trade tables in 250-row chunks, with
  hover highlighting and click-to-zoom on the chart.
- Runtime loading: `chart.render_loader()` and `chart.py loader` emit a page
  that fetches its spec from a URL (`?spec=` override supported).
- Compact payloads: `chart.encode_block()` and `render(..., compact=True)`
  base64-encode time/price integers for roughly 3x smaller JSON, decoded
  transparently by the viewer.
- Standard-library-only test suite (`tests/test_chart.py`, 33 tests).

### Notes

- The viewer vendors Lightweight Charts and opens zoomed out on every page.
- Volume is omitted from the payload by default; enable with
  `include_volume=True`.
- Readable JSON remains the default payload format; compact encoding is
  opt-in.

[Unreleased]: https://github.com/Jasoncoolboy/chartlab/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Jasoncoolboy/chartlab/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Jasoncoolboy/chartlab/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Jasoncoolboy/chartlab/releases/tag/v0.1.0
