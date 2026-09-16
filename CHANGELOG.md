# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Jasoncoolboy/chartlab/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Jasoncoolboy/chartlab/releases/tag/v0.1.0
