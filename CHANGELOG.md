# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Legacy backtest cost defaults (`CostConfig`) are now FTMO's measured XAUUSD costs: commission 0.0007 % of notional
  per side (new field `commission_pct_side`, added to the flat `commission_per_side_per_lot`), slippage $0.05 per fill,
  swap -83 / -8.3 USD per lot per night. Source: FTMO account probe 2026-09-18 and MT5 tester cost probe 2026-09-23
  (`testEGEA/results/mt5/egbook_costprobe.csv`). The engine is still a demo — see todo.md item 6.

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
