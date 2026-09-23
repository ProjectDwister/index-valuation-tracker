# Index Valuation Tracker

Cloud-updated valuation dashboard for selected NSE equity indices.

Live site: https://projectdwister.github.io/index-valuation-tracker/

## What the tracker does

The GitHub Action refreshes the canonical NIFTY 50 model and the multi-index dataset, writes the website data under `docs/data/`, generates one Excel workbook per eligible index, evaluates multi-index email alerts, commits refreshed data back to `main`, and deploys `docs/` to GitHub Pages. It also refreshes official NSE Indices constituent weightage data and adds a `Composition` worksheet to each per-index Excel workbook.

The scheduled run is Monday-Friday at 7:00 PM Asia/Kolkata. It can also be run manually from **Actions -> Refresh Index Valuation Tracker and deploy Pages -> Run workflow**.

## Dashboard universe

The website intentionally excludes short-history or incomplete indices. An index must have:

- a current P/E;
- a calculable composite score;
- at least 8 comparable post-2021 monthly P/E observations;
- at least 32 P/E-bearing quarter-end observations; and
- at least 16 matured 3-year forward-return observations.

Raw monthly and quarterly archive data are retained even for excluded indices, so an index can enter the dashboard automatically once it has enough history.

NIFTY 50 is a special case: its backtest uses the canonical long-history file from 1999 onward rather than the shorter common multi-index archive.

## Scoring

`Composite Score = 70% x Valuation Score + 30% x Earnings-Growth Score`

- BUY: score >= 70
- HOLD: 40 <= score < 70
- SELL: score < 40

Each index uses its own P/E history and its own implied EPS growth. Implied index EPS is calculated as index level divided by index P/E.

## Historical backtest methodology

The current historical return tables rank P/E within the applicable standalone/consolidated methodology regime and then pool observations into valuation quintiles. This is a descriptive regime-normalised historical study.

The current tables are intentionally left unchanged. A separate future research step can compare them with strict expanding-history/no-lookahead percentiles before any methodology change is adopted.

## Website

GitHub Pages is served only from `docs/`.

Important website files:

- `docs/index.html`
- `docs/styles.css`
- `docs/app.js`
- `docs/data/` generated JSON/CSV and the maintainable NSE holiday calendar
- `docs/downloads/indices/` dynamic per-index Excel downloads

The base URL opens NIFTY 50. Direct links such as `?index=nifty-bank` open the selected index.

## Market status indicator

The top-right market-status indicator uses Asia/Kolkata time, regular NSE cash-market hours (09:15-15:30), weekends, and the holiday calendar in:

`docs/data/nse_market_holidays.json`

Update that JSON when NSE publishes a new year's Capital Market holiday calendar. If the current year is not covered, the indicator fails conservatively to red and shows that a calendar update is required rather than incorrectly showing the market as open.

## Email alerts

The authoritative alert engine is the multi-index engine in `multi_index_tracker.py`.

It monitors every eligible displayed index and sends a consolidated email when one or more indices meet the configured threshold-change or signal-change conditions. The legacy NIFTY-only alert engine remains in `nifty_tracker_update.py` only for backwards compatibility and is disabled by default.

Required GitHub Actions secrets:

- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `ALERT_EMAIL_TO`

Optional repository variables include `ALERT_PE_DELTA`, `ALERT_INDEX_DELTA_PCT`, `SMTP_HOST`, `SMTP_PORT`, and `SMTP_FROM`.

## Main repository files

- `multi_index_tracker.py` - multi-index data, backtests, downloads and alerts
- `nifty_tracker_update.py` - canonical NIFTY 50 refresh and fallback web data
- `NIFTY_Valuation_Backtest_Live_Tracker.xlsx` - canonical NIFTY 50 workbook
- `nifty_quarterly_backtest.csv` - canonical NIFTY 50 long-history quarter-end study
- `multi_index_quarterly.csv` - persistent raw multi-index quarter-end archive
- `multi_index_monthly.csv` - persistent raw multi-index month-end archive
- `.github/workflows/nifty-tracker.yml` - scheduled refresh and Pages deployment
- `requirements.txt` - Python dependencies

## Data sources

- NSE / NSE Indices daily archive and index reports
- P/E methodology: https://www.niftyindices.com/resources/index-concepts/price-earnings-ratio
- NSE index reports: https://www.niftyindices.com/reports

Do not store passwords, API keys or confidential information in `docs/` because GitHub Pages is public.


## Composition and weightage
See `README_COMPOSITION.md` for the official NSE Indices monthly composition pipeline.
