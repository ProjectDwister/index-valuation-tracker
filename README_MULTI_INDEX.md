# NIFTY Multi-Index Valuation Tracker update

This update keeps the existing NIFTY 50 Excel model and Gmail threshold alerts, and adds a multi-index valuation/backtest layer to the GitHub Pages dashboard.

## Add / replace these files

Replace:
- `docs/index.html`
- `docs/styles.css`
- `docs/app.js`
- `.github/workflows/nifty-tracker.yml`

Add:
- `multi_index_tracker.py` at the repository root

Do not delete the existing:
- `nifty_tracker_update.py`
- `NIFTY_Valuation_Backtest_Live_Tracker.xlsx`
- `nifty_quarterly_backtest.csv`
- Gmail repository secrets
- existing `docs/data/` history

## First run

After committing the files, go to:

Actions -> Refresh NIFTY tracker and deploy Pages -> Run workflow

The first multi-index run is intentionally longer because it bootstraps historical NSE snapshots:
- Quarter-end observations from Mar-2012 onward for forward-return backtests
- Month-end observations from Apr-2021 onward for current P/E percentiles

It creates automatically:
- `multi_index_quarterly.csv`
- `multi_index_monthly.csv`
- `docs/data/multi_index_catalog.json`
- `docs/data/multi_index_latest.json`
- `docs/data/multi_index_backtests.json`
- `docs/data/multi_index_history.csv`

Later weekday runs are much lighter. New month-end and quarter-end observations are added only when required.

## Dashboard

The dashboard adds:
- Index selector with Broad Market, Sectoral and Strategy/Thematic/Other groups
- Bookmarkable URLs such as `?index=nifty-50`
- Per-index Overview / Valuation / Backtest / History tabs
- Cross-index valuation heatmap
- Per-index score based on its own history, not NIFTY 50's P/E range
- Only indices with sufficient live P/E history and a usable historical backtest are shown; limited-history / P/E-unavailable indices are excluded from the webpage

## Scoring

For indices with sufficient data:
- 70% valuation score based on the index's own post-2021 P/E percentile
- 30% YoY implied EPS-growth score
- BUY >= 70
- HOLD 40 to <70
- SELL <40

Historical forward-return tables use quarter-end observations and separate pre-/post-2021 P/E regimes.

## Current scope

The NSE daily multi-index archive contains many NIFTY indices in one file. The updater keeps equity-like NIFTY indices and excludes categories where a P/E-based model is not economically appropriate (e.g. bonds/G-Secs, debt, inverse/leverage, futures/arbitrage and hybrid debt strategies).


## Useful-history filter

The webpage now includes only indices that satisfy all of the following:
- Current P/E is available
- A live composite score can be calculated
- At least 8 comparable post-2021 monthly P/E observations
- At least 12 P/E-bearing quarter-end observations for the backtest

Indices that do not yet meet these conditions are omitted from the selector and heatmap. Their raw monthly and quarterly source history is retained, so they can automatically appear later when enough history has accumulated.
