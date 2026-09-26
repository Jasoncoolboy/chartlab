# ChartLab

Offline TradingView-style HTML charts and setup pages for any system, from FTMO (priceData) or Dukascopy bars;
true UTC inside, Malaysian time on screen. Source of truth: `README.md`, `docs/AI_INTEGRATION.md`,
`NEXT-SESSION.md`. A page is a picture, not evidence.

## ⛔⛔ FTMO bars and costs come from priceData ONLY (owner rule, 2026-09-26)

⏭ **NEXT SESSION PRIORITY (owner, 2026-09-27): execute the switch below before any other work here.** The order across
projects and the routine to follow: `C:\personalCode\priceData\todo.md` "NEXT SESSION — DO THIS FIRST" (this project is #9).

Every FTMO price and every FTMO cost used here comes from **`C:\personalCode\priceData`** and nowhere else:

* **Bars:** `PriceData(r"C:\personalCode\priceData").load(sym, tf)` (or `data/clean/{SYM}/{SYM}_{TF}.parquet`):
  FTMO server time (EET/EEST on the US DST calendar), **BID**, 9 symbols × M1..MN, every bar verified against M1.
  It holds **only the verified-clean years, from `clean_from` = 2022-01-01**. The repaired 2019-21 bars are in
  `data/pre_clean/` and come back only with `load(..., include_pre_clean=True)`: charts and indicator warm-up, never a
  tested or quoted number. To true UTC: `chartlab.sources.ftmo_server_to_utc`.
* **Costs:** `price_data.COST_MODELS`, `commission_pips(sym, price)`, `all_in_pips(sym, price, market_fills)`,
  `PriceData.spread_pip(sym, year)`; every number and its evidence is in `priceData/docs/COSTS.md`. Charge the spread
  once per round trip, commission on both deals (metals and BTCUSD as a % of notional, so pass the price), and slippage
  per market fill. ⛔ Never cost from `SpreadPts`: it is the bar's MINIMUM tick spread.
* This project's own CSV/parquet copies, Dukascopy downloads, MT5 exports and cost constants are **superseded**. If a
  number here differs from priceData, priceData wins: re-cost or re-run before quoting. A symbol priceData lacks is added
  there first (its README, "Add a New Symbol"). Dukascopy is allowed only as an independent cross-check.
* Any number already recorded here off pre-2022 data, another data source or another cost table is **out of rule**.
  Say so whenever it is quoted.
* Refresh: `python C:\personalCode\priceData\scripts\update_data.py` (terminal closed). Year verdicts:
  `priceData/docs/DATA_QUALITY.md`.

**What this project must switch** (from a code search, 2026-09-27; nothing here has been changed yet):

1. **`pricedata.py`** reads `data/clean/{SYM}/{SYM}_{TF}.parquet` directly. Since 2026-09-26 that is **2022+ only**. A page
   that needs older bars for context must ask for `data/pre_clean` explicitly (e.g. `PriceData.load(..., include_pre_clean=True)`),
   and the page should say those bars are pre-clean (repaired, never tested). Verified 2026-09-27 on the split data: the
   real-data classes of `tests/test_sources.py` (9, including the GBPUSD US-vs-EU DST check against real Dukascopy, n > 400
   in 2022-26 alone) and `tests/test_pipeline.py` (42) pass.
2. **Costs:** the `config.py` `BacktestConfig` defaults (`commission_pct_side`, `slippage_per_side_usd`, the swaps) are
   hand copies of priceData's XAUUSD figures, and a copied number drifts. Read them from `COST_MODELS`. The legacy
   `engine.py` / `strategies.py` / backtest stay a demo; never quote them.
3. **Dukascopy** (`dukascopy.py`, `data.py`) stays: ChartLab charts either source and identifies which. An **FTMO** page
   is always built from priceData.

**Pre-2022 dates:** none in the library code. `tests/test_sources.py` loops the DST-disagreement weeks over 2019-2026, and
only 2022+ exists in `data/clean` now; it still passes. `tests/test_pipeline.py` has a synthetic 2020 row (fixture).
