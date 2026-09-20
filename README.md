# ChartLab

ChartLab turns a plain JSON description of price data into an **offline, interactive,
TradingView-style HTML chart** — no CDN, no network, no server required.

It comes in three layers:

1. **The generator** (`chart.py` + `assets/`) — standard library only. Copy these two
   things into any project and you can emit charts. See
   [`docs/CHART_SPEC.md`](docs/CHART_SPEC.md) and
   [`docs/AI_INTEGRATION.md`](docs/AI_INTEGRATION.md).
2. **The data adapters** (`sources.py`, `pricedata.py`, `export.py`, `setups.py`) — read
   the two data sources this research uses, **FTMO** (`C:\personalCode\priceData`) and
   **Dukascopy**, identify which one a file is, convert everything to true UTC, build
   charts and per-setup pages, and turn **another system's setup rows** into pages.
   Requires `pandas`, `numpy`, `pyarrow`.
3. **A legacy research pipeline** (`data.py`, `dukascopy.py`, `engine.py`, `strategies.py`,
   `metrics.py`) — a Dukascopy XAUUSD backtester. ⛔ Its costs are **not** the measured
   FTMO costs; do not quote its numbers (see [Legacy pipeline](#legacy-pipeline)).

## Time: UTC inside, Malaysian time on screen

**Everything inside ChartLab is true UTC** — every frame index, every spec time, every setup.
Session timings (Asia / London / New York) are UTC based, so a downstream system can do
`df.index.hour` on a ChartLab frame and mean it. **Malaysian time (MYT, UTC+8, no DST) is only
a display setting**: the page opens in MYT and has **MYT | UTC** buttons in the toolbar. The
data is never rewritten; switching only changes how the axis, crosshair, trades drawer, header
and footer show times (the footer names the zone). `?tz=UTC` in the URL opens it in UTC, the
spec's `tz` field sets the starting zone, and the viewer remembers the last choice.

| source | how it is recognised | labels in the raw data | ChartLab does |
|---|---|---|---|
| **`ftmo`** (priceData) | `df.attrs['source']`, `SpreadPts` column | FTMO server time: **UTC+2, UTC+3 during US DST** | converts to UTC |
| **`dukascopy`** | tag, `bid_*`/`ask_*` columns, website CSV `Gmt time`, weekend fingerprint | UTC | nothing |

**The FTMO rule** — server time is UTC+3 from the 2nd Sunday of March 07:00Z to the 1st Sunday
of November 06:00Z, UTC+2 otherwise (FTMO follows the US calendar: its daily bar opens at 17:00
New York). It was checked, not assumed: converted FTMO H1 matches **real Dukascopy H4 at the
same UTC instant** to 0.79 % of the bar range (XAUUSD, 9,905 buckets); in the weeks where the
US and EU DST calendars disagree it is 0.69 % off against 20.0 % for the EU rule (GBPUSD);
and every converted D1 bar of all 9 symbols (~2,000 each) opens at exactly 17:00 New York.
`tests/test_sources.py` re-runs all three.
BTCUSD trades through the switch weekends, so its few switch-hour bars that land on one UTC
stamp are merged (counted in `df.attrs['utc_conversion']`), never dropped.

**Times you hand in carry a clock too.** Trades, zones, equity and setup rows have times, and
a wrong guess is a silent 2–3 hour error, so `clock` is **required** whenever you pass any:
`ftmo` (FTMO server time — what an FTMO-based system's own timestamps are), `utc`, or `myt`.
A time that carries its own zone (`…Z`, `+08:00`, an aware datetime) is absolute and unaffected.

### Identifying FTMO vs Dukascopy

`--source auto --data FILE` (or `sources.detect(df)` / `sources.resolve(df)`) reads, in order:
an explicit tag → the column shape → a **weekend fingerprint** (FTMO server-time labels of
FX/metals never fall on a Sunday — 0.00 % across the 8 FX/metal symbols at every timeframe M1–D1; UTC labels do,
the Sunday-evening open — 3.2 % of H4 and 16.6 % of D1 bars in real Dukascopy data), and prints
the evidence. It **never guesses silently**: a tag-less frame that only looks like a weekday-only
feed, crypto (which trades weekends) and weekly frames give no answer and are refused with
`--assume ftmo|dukascopy` as the way out. A declared source that contradicts a tagged frame is
refused, and a frame already converted to UTC is never converted twice.

## What the chart page gives you

- Candlesticks across multiple timeframes with a timeframe switcher.
- **MYT / UTC display switch** (see above).
- Prices at the instrument's own precision — 5-digit FX, 3-digit JPY, 2-digit gold —
  inferred from the bars, or set with the spec's `precision` field.
- Trades (entry/exit, SL/TP) and rectangular **zones** drawn on the price axis.
- Provider indicators (SMA / EMA / Bollinger / RSI / MACD) as toggle chips — never user-added.
- Optional metrics panel (`stats`) and a **Trades** drawer listing every overlay trade.
- Optional equity pane synchronised with the main chart.
- Optional volume histogram, shown only when the spec carries a `volume` series.
- Three drawing tools: **Hand**, **Rectangle**, **Horizontal line**.
- Opens fully zoomed out; scroll to zoom, drag to pan, arrow keys to step.
- Debug handle in the console: `window.ChartLab.debug()` (includes `tz` and `tzOffset`).

Commands below use `python`. On macOS/Linux use `python3`; on Windows `python3` is often
only a Microsoft Store shim, so `python` is the safe spelling.

## Quick start (generator only, no dependencies)

Extract the zip and run from inside the folder:

```bash
# Render the bundled example spec
python chart.py render examples/sample_spec.json out.html

# Bundle everything into one shareable file (no lib/ folder needed)
python chart.py render examples/sample_spec.json out.html --inline-lib

# Build a page straight from CSV files (optional zones/stats/indicators)
python chart.py from-csv \
  --bars bars.csv --trades trades.csv --equity equity.csv \
  --zones zones.json --stats stats.json --inds "SMA50,RSI14,MACD" \
  --symbol XAUUSD --timeframe D1 --out page.html

# Check a spec before rendering
python chart.py validate examples/sample_spec.json

# Roughly 3x smaller page (bars base64-encoded, not human-readable)
python chart.py render examples/sample_spec.json out.html --compact

# A page that fetches its spec JSON at runtime (must be served over HTTP)
python chart.py loader specs/xau.json loader.html --title "XAUUSD"
```

`python -m chart <command>` is equivalent if the current directory is on `sys.path`.

The generator does not know which clock your numbers are in: **its times are UTC epochs**
(a naive timestamp is read as UTC). Convert first if yours are not.

```python
from chart import spec, render, validate

s = spec(
    "XAUUSD",
    {"D1": {"time": [1704067200, 1704153600],          # epoch seconds, UTC
            "open": [2050, 2054], "high": [2056, 2060],
            "low":  [2048, 2051], "close": [2054, 2058]}},
    trades=[{"dir": "long", "entryTime": 1704067200, "entryPrice": 2050,
             "exitTime": 1704153600, "exitPrice": 2058, "net": 400}],
    zones=[{"start": 1704067200, "end": 1704153600, "low": 2048, "high": 2060,
            "label": "Range"}],
    tz="MYT",                                          # how the page DISPLAYS them (default)
)
assert not validate(s)
render(s, "xauusd_d1.html", title="XAUUSD D1")
```

### Adopting the generator in another project

Copy just the reusable unit — nothing else is required:

```bash
cp chart.py  /path/to/your_project/
cp -r assets /path/to/your_project/
```

Then `import chart` and call `render(...)`. The viewer template and the vendored
Lightweight-Charts build live in `assets/`; you can point elsewhere with
`render(s, out, viewer=..., lib=...)`.

## Using it from another system (FTMO or Dukascopy data)

**FTMO bars come from `C:\personalCode\priceData`** (`data/clean/{SYM}/{SYM}_{TF}.parquet`,
native bars, 9 symbols × M1…MN). Override with `PRICEDATA_ROOT` or `root=`. The loader refuses
a file that is unsorted, has duplicate stamps, NaNs, incoherent OHLC or a timezone. Frames and
`start=`/`end=` windows you get back are **UTC**.

### Route A — Python, in-process

```python
import sys
sys.path.insert(0, r"C:\personalCode")              # the folder that CONTAINS "chartlab"
from chartlab import pricedata, chart

spec = pricedata.build_spec(
    "EURUSD", ["M15", "H1", "H4", "D1"],            # native bars, converted to UTC
    max_bars=3000,                                  # most recent N per timeframe
    clock="ftmo",                                   # REQUIRED with zones/trades: their clock
    zones=[{"start": "2026-09-15 08:00", "end": "2026-09-15 12:00",
            "low": 1.1600, "high": 1.1620, "label": "my zone"}],
    trades=[{"dir": "long", "entryTime": "2026-09-15 12:15", "entryPrice": 1.16210,
             "sl": 1.1605, "tp": 1.1650}],
    indicators="EMA50,RSI14",
    tz="MYT")                                       # display zone the page opens in
chart.render(spec, r"C:\out\eurusd.html", inline_lib=True)
```

The same frames, for your own session logic (`df.index.hour` is UTC):
`pricedata.load_frame("EURUSD", "M15")`; `clock="server"` returns FTMO's raw labels instead.

Only need the renderer and have your own bars? `sys.path.insert(0, r"C:\personalCode\chartlab")`
then `import chart` — standard library only, no pandas (times must be UTC epochs).

### Route B — CLI (any language)

```bash
python run.py chart --symbol EURUSD --timeframe M15 --extra-tfs H1 H4 D1 \
  --max-bars 3000 --inds "EMA50,RSI14" --volume --name eurusd_m15

# a file from either source: identify it, print the evidence, convert to UTC, chart it
python run.py chart --source auto --data XAUUSD_H4.csv --symbol XAUUSD
python run.py chart --source auto --assume ftmo --data mystery.parquet --symbol EURUSD
```

`--source` is `ftmo` (default; reads the priceData folder — `pricedata` is the old name),
`dukascopy` (a local M1 parquet from `--data`) or `auto`. Pages land in `out/charts/`
(git-ignored). Add `--trades-file/--zones-file/--equity-file/--stats-file` for overlays
(then `--clock` is required), `--tz UTC` to open in UTC. `--start/--end` are UTC.

### Setups from another system's rows

Your engine writes a JSON list of setups; ChartLab draws one page each (bars around the
trigger, the trade with SL/TP, the zone(s), a timeframe button per `--extra-tfs`) plus a catalog:

```bash
python run.py rows --rows my_setups.json --symbol EURUSD --timeframe M15 \
  --clock ftmo --extra-tfs H1 H4 D1 --pre 60 --post 45 --label "my system" \
  --out C:\somewhere\my_system_pages          # absolute = self-contained folder
```

or in Python: `setups.setups_from_rows(rows, "M15", clock="ftmo", symbol="EURUSD")` →
`setups.render_pages(df, found, out_dir, symbol="EURUSD", extra_frames={...}, tz="MYT")`.
`--clock` (`ftmo` = your rows are in FTMO server time, `utc`, `myt`) is required.

| key (aliases) | | |
|---|---|---|
| `dir` (`direction`, `side`) | required | `long`/`short`, `1`/`-1`, `buy`/`sell` |
| `entry_time` (`fill`) | required | epoch s, ISO string or datetime, in `--clock` |
| `entry_price` (`entry`, `price`) | required | |
| `arm` (`trigger_time`, `time`) | | centres the window; default = entry time |
| `stop` (`sl`), `target` (`tp`) | | a long stop must be below the entry, target above (reverse for shorts) — otherwise the row is refused |
| `zone` (`zones`) | | one zone or a list: `start`, `end`, `low`, `high`, optional `label`, `color` |
| `label`, `id` (`ref`), `symbol`, `lots` | | ids become file names; duplicates are refused |
| `exit_time`, `exit_price`, `net`, `reason` | | draws the outcome when present |

What this is **not**: a zone is one rectangle here. If a page must show a zone's life stages
(area → formed → live → spent) or engine-drawn refs/bands, use the MSNR_ea reference generator
described in the `setup-chart-generator` skill.

`--extra-tfs` views are cut to the setup's window: the left edge is the bar *containing* the
window start (widened to 40 of that timeframe's own bars), the right edge is never extended,
and a view over 5,000 bars is skipped. A higher-timeframe candle that contains the trigger is
drawn with its final high/low — that is a review page showing the outcome, not what was
knowable at the trigger. A setup whose trigger falls outside the bars is refused rather than
clamped. Setup ids built by the finders (`donch20-202601042200-S`) carry **UTC** stamps.

The built-in `python run.py setup --kind donchian|macross ...` finders take the same
`--symbol/--extra-tfs/--root/--source/--tz` flags and are demonstration strategies, not
research results.

## Legacy pipeline

`download`, `build`, `resample`, `backtest` (and `--source dukascopy` on `chart`/`setup`)
use a local Dukascopy XAUUSD M1 parquet under `data/parquet/` (UTC). ⛔ The backtest defaults
are **not** FTMO's: no commission, $0.02 slippage, swap −14/−4 per lot (FTMO measured: gold
swap −83/−8.3 pts, commission 0.0007 % of notional per side). Do not quote its numbers; use the
`backtest-method` skill for costed results.

```bash
python run.py download --start 2022-01-01
python run.py build    --start 2022-01-01
python run.py resample
python run.py backtest --strategy donchian --timeframe D1 --n 20 --sl-atr 2 --tp-atr 2 --lots 0.5
python run.py chart --source dukascopy --timeframe D1 --extra-tfs H4 W1 \
  --trades-file out/trades_donchian_D1.csv --equity-file out/equity_donchian_D1.csv \
  --stats-file out/metrics_donchian_D1.json --name donchian_D1
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
│   ├── test_chart.py               # generator, precision, display timezone (stdlib only)
│   ├── test_pipeline.py            # export / priceData adapter / setups (needs pandas)
│   └── test_sources.py             # FTMO<->UTC clock, identification, real-Dukascopy cross-check
├── sources.py          # FTMO vs Dukascopy: identification, UTC conversion, overlay clocks
├── pricedata.py        # FTMO priceData loader (strict, UTC) + build_spec
├── export.py           # frames -> spec blocks (priceData / lowercase / bid_ columns)
├── setups.py           # setup finders, rows-from-any-system, per-setup pages + catalog
├── cli.py              # the `python run.py ...` entry point
├── config.py           # dataclass configs (legacy costs, backtest, data paths)
├── dukascopy.py        # legacy: M1 tick download + bi5 decode
├── data.py             # legacy: parquet IO + timeframe resampling
├── strategies.py       # Donchian / MA-cross signal logic (vectorizable)
├── engine.py           # legacy XAUUSD trade lifecycle + daily equity
└── metrics.py          # performance statistics
```

The modules use relative imports, so the repository root is registered as the `chartlab`
package by `run.py`; from Python, put the folder that *contains* `chartlab/` on `sys.path`.

## Design notes

- **The JSON spec is the contract.** Any system or model that can write JSON can
  produce a chart; the renderer never needs to understand the strategy.
- **Time is epoch seconds UTC** — always. `tz` (UTC/MYT) is display only, and `source`
  (`ftmo`/`dukascopy`) records where the bars came from.
- **Prices are never rounded below the instrument's own digits.** `precision` (0–8) is
  inferred from the bars (scanning every value, floor 2) and drives the axis, legends,
  trade labels and compact encoding. A fixed 4 dp is one whole pip on a 5-digit pair.
- **Pages stay small**: `render(..., compact=True)` stores prices as int32 at `10**precision`
  (scale carried in the block, ~3x smaller); a price that would overflow raises rather than
  rounding silently. Nested catalogs share a single `lib/` via `lib_dir="../../lib"`.
- **Loader pages** (`render_loader` / `chart.py loader`) fetch their spec JSON at runtime,
  so one page can always show the latest data; `?spec=<url>` overrides the URL.
- **Volume is opt-in**: pass `volume=True` to `pricedata.build_spec` (or `chart --volume`);
  when a series is present the viewer shows the histogram pane and toggle.
- **Indicators are opt-in**: `SMA`/`EMA`/`BB`/`RSI`/`MACD` chips appear only for
  indicators the spec defines (`--inds "SMA50,RSI14,MACD"`).
- **Nothing here checks look-ahead.** A page shows what it is given; the system that produced
  the setups must prove they are causal.

## Tests

```bash
python -m unittest discover -s tests -v
```

`test_pipeline.py` and `test_sources.py` skip themselves without pandas/pyarrow; their
real-data classes read the priceData folder and the packaged Dukascopy library
(`C:\personalCode\mtf-regime-engine-v22.4\data\library`) and skip when those are absent.

## Documentation

- `docs/CHART_SPEC.md` — every field of the JSON spec.
- `docs/AI_INTEGRATION.md` — the minimal contract for other systems/AIs.
- `CHANGELOG.md` — release history.

## License

MIT — see `LICENSE`.
