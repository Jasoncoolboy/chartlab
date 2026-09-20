# ChartLab

ChartLab turns a plain JSON description of price data into an **offline, interactive,
TradingView-style HTML chart** — no CDN, no network, no server required.

It comes in two layers:

1. **The generator** (`chart.py` + `assets/`) — standard library only. Copy these two
   things into any project and you can emit charts. See
   [`docs/CHART_SPEC.md`](docs/CHART_SPEC.md) and
   [`docs/AI_INTEGRATION.md`](docs/AI_INTEGRATION.md).
2. **The research pipeline** (`data.py`, `engine.py`, `strategies.py`, `export.py`, …) —
   the real-data backtest workflow that produced the example charts. Requires
   `pandas`, `numpy`, `pyarrow`.

## What the chart page gives you

- Candlesticks across multiple timeframes with a timeframe switcher.
- Trades (entry/exit, SL/TP) and rectangular **zones** drawn on the price axis.
- Provider indicators (SMA / EMA / Bollinger / RSI / MACD) as toggle chips — never user-added.
- Optional metrics panel (`stats`) and a **Trades** drawer listing every overlay trade.
- Optional equity pane synchronised with the main chart.
- Optional volume histogram, shown only when the spec carries a `volume` series.
- Three drawing tools: **Hand**, **Rectangle**, **Horizontal line**.
- Opens fully zoomed out; scroll to zoom, drag to pan, arrow keys to step.
- Debug handle in the console: `window.ChartLab.debug()`.

## Quick start (generator only, no dependencies)

Extract the zip and run from inside the folder:

```bash
# Render the bundled example spec
python3 chart.py render examples/sample_spec.json out.html

# Bundle everything into one shareable file (no lib/ folder needed)
python3 chart.py render examples/sample_spec.json out.html --inline-lib

# Build a page straight from CSV files (optional zones/stats/indicators)
python3 chart.py from-csv \
  --bars bars.csv --trades trades.csv --equity equity.csv \
  --zones zones.json --stats stats.json --inds "SMA50,RSI14,MACD" \
  --symbol XAUUSD --timeframe D1 --out page.html

# Check a spec before rendering
python3 chart.py validate examples/sample_spec.json

# Roughly 3x smaller page (bars base64-encoded, not human-readable)
python3 chart.py render examples/sample_spec.json out.html --compact

# A page that fetches its spec JSON at runtime (must be served over HTTP)
python3 chart.py loader specs/xau.json loader.html --title "XAUUSD"
```

`python3 -m chart <command>` is equivalent if the current directory is on `sys.path`.

To use it from another Python program:

```python
from chart import spec, render, validate

s = spec(
    "XAUUSD",
    {"D1": {"time": [1704067200, 1704153600],
            "open": [2050, 2054], "high": [2056, 2060],
            "low":  [2048, 2051], "close": [2054, 2058]}},
    trades=[{"dir": "long", "entryTime": 1704067200, "entryPrice": 2050,
             "exitTime": 1704153600, "exitPrice": 2058, "net": 400}],
    zones=[{"start": 1704067200, "end": 1704153600, "low": 2048, "high": 2060,
            "label": "Range"}],
)
assert not validate(s)
render(s, "xauusd_d1.html", title="XAUUSD D1")
```

### Adopting it in another project

Copy just the reusable unit — nothing else is required:

```bash
cp chart.py  /path/to/your_project/
cp -r assets /path/to/your_project/
```

Then `import chart` and call `render(...)`. The viewer template and the vendored
Lightweight-Charts build live in `assets/`; you can point elsewhere with
`render(s, out, viewer=..., lib=...)`.

## Full pipeline (this repository)

Requires `pandas`, `numpy`, `pyarrow` and a local M1 parquet file under `data/parquet/`.
From the repository root, drive the pipeline with the bundled launcher (`run.py`)
or, when the package is nested inside a project, `python3 -m chartlab`:

```bash
# Download / decode Dukascopy M1 ticks into parquet
python3 run.py download --start 2022-01-01
python3 run.py build    --start 2022-01-01

# Resample M1 into M5..W1
python3 run.py resample

# Costed backtest (retail gold CFD cost model in configs/)
python3 run.py backtest --strategy donchian --timeframe D1 \
  --n 20 --sl-atr 2 --tp-atr 2 --lots 0.5

# Render the result to an interactive page
python3 run.py chart --timeframe D1 --extra-tfs H4 W1 \
  --trades-file out/trades_donchian_D1.csv \
  --equity-file out/equity_donchian_D1.csv \
  --stats-file out/metrics_donchian_D1.json \
  --inds "EMA50,SMA200,RSI14,MACD" --volume \
  --name donchian_D1 --exchange "retail CFD"

# Build a catalog of annotated setup pages
python3 run.py setup --kind donchian --timeframe H4 --n 20 \
  --sl-atr 1.5 --tp-atr 3 --limit 15 --out setups/donch_H4
```

## Package layout

```
chartlab/
├── run.py              # launcher: run the pipeline from the repository root
├── LICENSE             # MIT
├── CHANGELOG.md        # release history
├── chart.py            # stdlib-only generator: spec -> HTML  (the copyable unit)
├── assets/
│   ├── viewer.html                 # TradingView-style viewer template
│   └── lightweight-charts...js     # vendored charting library
├── docs/
│   ├── CHART_SPEC.md               # full JSON schema reference
│   └── AI_INTEGRATION.md           # how to wire it into another system
├── examples/
│   └── sample_spec.json            # a valid, renderable spec
├── tests/
│   └── test_chart.py               # unittest suite for the generator
├── config.py           # dataclass configs (costs, backtest, data paths)
├── dukascopy.py        # M1 tick download + bi5 decode
├── data.py             # parquet IO + timeframe resampling
├── strategies.py       # Donchian / MA-cross signal logic (vectorizable)
├── engine.py           # MT5-portable trade lifecycle + daily equity
├── metrics.py          # performance statistics
├── export.py           # engine output -> spec blocks (trades, equity, bars)
├── setups.py           # find + annotate historical setup pages
└── cli.py              # the `python3 -m chartlab ...` entry point
```

Repository-root helper: `run.py` is a thin launcher that lets you run the
pipeline (`python3 run.py backtest ...`) directly from the repository root
without installing the package.

## Design notes

- **The JSON spec is the contract.** Any system or model that can write JSON can
  produce a chart; the renderer never needs to understand the strategy.
- **Time is epoch seconds UTC**, which is what Lightweight-Charts expects.
- **Pages stay small**: `render(..., compact=True)` base64-encodes bars (~3x smaller);
  nested catalogs share a single `lib/` via `lib_dir="../../lib"`.
- **Loader pages** (`render_loader` / `chart.py loader`) fetch their spec JSON at runtime,
  so one page can always show the latest data; `?spec=<url>` overrides the URL.
- **Volume is opt-in**: pass `include_volume=True` to `export.bars_block` (or
  `chart --volume`); when a series is present the viewer shows the histogram pane
  and toggle, otherwise the control stays hidden.
- **Indicators are opt-in**: `SMA`/`EMA`/`BB`/`RSI`/`MACD` chips appear only for
  indicators the spec defines (`--inds "SMA50,RSI14,MACD"`).
- **Engine performance**: signals use precomputed ATR and vectorized calendar
  fields, so a full D1 backtest over 1.6M M1 bars runs in ~13s instead of ~49s,
  with byte-identical trade and metric output.
- **MT5 portability**: `engine.py` uses only closed-bar decisions and next-bar
  fills with bid/ask handling, so the same logic maps directly to MQL5.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Documentation

- `docs/CHART_SPEC.md` — every field of the JSON spec.
- `docs/AI_INTEGRATION.md` — the minimal contract for other systems/AIs.
- `CHANGELOG.md` — release history.

## License

MIT — see `LICENSE`.
