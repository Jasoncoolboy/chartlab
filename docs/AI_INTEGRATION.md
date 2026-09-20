# Integrating ChartLab into another system

ChartLab is designed to be adopted by other applications and AI agents with as little
coupling as possible. There are exactly two contracts:

1. **Input**: a JSON *chart spec* (or plain CSV files) describing bars, trades,
   zones, equity, and indicators.
2. **Output**: an offline `.html` file plus (optionally) a sibling `lib/` directory.

Nothing about your strategy, database, or language leaks into the renderer.

## The minimal contract

A producer only has to provide, per timeframe, five parallel arrays anchored on
**epoch seconds (UTC)**:

```
time[], open[], high[], low[], close[]
```

Everything else — `overlay.trades`, `overlay.zones`, `equity`, `indicators`,
`stats`, `exchange`, `periodLabel` — is optional. The smallest possible spec is one
timeframe with one bar.

Full field reference: [`CHART_SPEC.md`](CHART_SPEC.md).

## Option A — call the CLI (language-agnostic)

Any process that can write a file and run a command can produce a chart:

```bash
# 1. Write spec.json  (from any language)
# 2. Render it
python3 chart.py render spec.json chart.html

# For a single portable file (no lib/ folder):
python3 chart.py render spec.json chart.html --inline-lib
```

Use this when the producer is not Python, or when you want a hard process boundary.

## Option B — call the Python API (in-process)

Copy `chart.py` and `assets/` into your project, then:

```python
from chart import spec, render, validate

s = spec("XAUUSD", {"D1": bars_block}, trades=trades, zones=zones,
         equity=equity, indicators=inds)
problems = validate(s)
if not problems:
    render(s, "chart.html")
```

`render` returns the output `Path`. It creates the output directory and copies the
chart library into `<out_dir>/lib/` automatically.

## Option C — CSV in, HTML out

If your system already emits CSV, skip JSON entirely:

```bash
python3 chart.py from-csv \
  --bars bars.csv --trades trades.csv --equity equity.csv \
  --symbol XAUUSD --timeframe D1 --period-label "D1 · XAUUSD" --out chart.html
```

Column names are auto-detected (e.g. `time`/`date`/`datetime`, `bid_open`, …).

## Option D — let the page fetch its spec at runtime

When the data changes often, or another service already serves the spec JSON, write a
loader page once and keep the JSON fresh:

```bash
python3 chart.py loader https://your-host/specs/xau.json chart.html
```

Open `chart.html` and it fetches that URL as JSON and renders it. Any loader page also
honours `?spec=<url>` to preview a different spec without re-rendering. Loader pages
must be served over HTTP/HTTPS (they cannot fetch from `file://`).

## Copying the reusable unit

Everything needed to render is:

```
chart.py
assets/viewer.html
assets/lightweight-charts.standalone.production.js
```

`cp` those into any project. No third-party Python packages are required — the
generator is standard library only. Verify with:

```bash
python3 -c "import chart; print(chart.SPEC_VERSION)"
```

## Steps an AI agent should follow

1. **Get bars as epoch seconds UTC.** If your source gives ISO timestamps, either
   convert them yourself or let `chart.to_epoch` do it (`"2024-01-01T00:00:00Z"` works).
2. **Sort ascending and keep arrays equal length.** `validate` rejects unsorted time
   and length mismatches.
3. **Round prices to 4 decimals** (as the built-in exporter does) to keep payloads small.
4. **Omit volume** unless you actually render it — the viewer has no volume pane.
5. **Prefer `values` for indicators** you already computed; use `type`+`period` only
   as a convenience fallback (`sma`, `ema`, `bb`, `rsi`, `macd`). `rsi`/`macd`
   render on their own price scale.
6. **`validate` before `render`**, and surface the returned problem list to the user.
7. **Pick the delivery mode**: default (HTML + `lib/`) or `--inline-lib` for a single
   file you want to email, embed, or ship in a container with no static server.
8. **Add `stats`** if you have headline metrics (return, Sharpe, drawdown, trades) — the
   viewer shows them in a hideable top-right panel; format the values yourself.
9. **Shrink large pages with `compact=True` (`--compact`)** when payload size matters
   more than the JSON being human-readable.

## Producing bars from common sources

- **SQL**: `SELECT unix_timestamp(ts) AS time, open, high, low, close ... ORDER BY ts`.
- **Pandas DataFrame**: convert the index with
  `(df.index.asi8 // 1_000_000_000).tolist()` for epoch seconds, then map columns.
- **MT5 / MQL5**: bar `time` is already seconds; export directly.
- **REST API**: normalize the timestamp to UTC epoch seconds on the producer side.

Multi-timeframe pages are just multiple keys under `timeframes`. Provide every
timeframe you want the switcher to offer; the viewer opens on `defaultTimeframe`
(or the timeframe with the most bars).

## Interpreting the output

- A chart is a pure function of the spec — no runtime data access, no network calls
  (loader pages fetch their own spec and locate the chart library beside the page).
- Pages written by `render`/`render_file` work from `file://` and from any static server.
- For diagnostics, the page exposes `window.ChartLab.debug()` in the browser console,
  returning `{ ready, tf, bars, first, last, trades, zones, inds, stats, drawerOpen, visible, ... }`.

## Performance and size guidance

| Concern | Guidance |
|---------|----------|
| Payload size | Roughly linear in bar count. Omit `volume` (saves ~20%). Round prices to 4 dp. |
| Big pages | `render(..., compact=True)` / `--compact` base64-encodes bars for ~3x smaller JSON. |
| Many pages | Generate one HTML per chart and use `chart.gallery(dir)` for an index. |
| Nested catalogs | Pass `lib_dir="../../lib"` so every page shares one copy of the chart library. |
| Sharing | `--inline-lib` makes one file; otherwise keep `chart.html` and `lib/` together. |
| Backtests | The bundled engine precomputes indicators; see `chartlab/engine.py`. |

A page with ~7,200 H4 bars is about 450 KB; a focused 2026 window is about 160 KB.

## What the viewer deliberately does NOT do

- It does not let users add indicators — indicators are provider-supplied and only toggleable.
- It only offers three drawing tools: Hand, Rectangle, Horizontal line.
- It never fetches data. If it is not in the spec, it is not on the chart.

Keeping these constraints is what makes the output deterministic and easy to embed.
