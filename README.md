# NIFTY 50 Valuation Signal — GitHub Actions + GitHub Pages

This repository runs the NIFTY valuation model in the cloud and publishes a mobile-friendly dashboard with GitHub Pages.

## What happens automatically

- **Weekdays at 7:00 PM India time**: GitHub Actions runs the Python updater.
- The updater refreshes NIFTY P/E, index close, implied EPS growth and the composite model score.
- It updates the Excel workbook and appends the daily signal history.
- It writes `docs/data/latest.json`, `docs/data/history.csv` and `docs/data/backtest_summary.json`.
- It copies the current Excel model to `docs/downloads/`.
- The workflow commits the refreshed history back to `main` and publishes the `docs/` site to GitHub Pages.
- You can also run it anytime from **Actions → Refresh NIFTY tracker and deploy Pages → Run workflow**.

## One-time GitHub setup

1. Create a new GitHub repository. `nifty-valuation-tracker` is a good name.
2. Upload/commit **all files and folders in this project**, preserving `.github/workflows/nifty-tracker.yml` and the `docs/` folder.
3. Make sure the default branch is named `main`.
4. Open **Settings → Pages**.
5. Under **Build and deployment → Source**, choose **GitHub Actions**.
6. Open **Actions**, select **Refresh NIFTY tracker and deploy Pages**, and choose **Run workflow** once.
7. After the deployment finishes, the Pages URL appears in the deployment summary and under **Settings → Pages**.

> GitHub Pages is a public website. This dashboard contains only market/model data, but do not add passwords, API keys or confidential information under `docs/`.

## Files

- `NIFTY_Valuation_Backtest_Live_Tracker.xlsx` — full Excel model and backtest.
- `nifty_tracker_update.py` — cloud/local updater.
- `nifty_quarterly_backtest.csv` — fixed historical quarter-end study.
- `requirements.txt` — Python dependencies.
- `.github/workflows/nifty-tracker.yml` — scheduled cloud job + Pages deployment.
- `docs/` — static website and generated data.
- `run_nifty_tracker.bat` — optional Windows local launcher; no longer required for the cloud setup.

## Signal definition

`Composite Score = 70% × Valuation Score + 30% × Earnings-Growth Score`

- **BUY**: score ≥ 70
- **HOLD**: 40 ≤ score < 70
- **SELL**: score < 40

The current P/E is ranked within the post-April-2021 consolidated-earnings regime. The historical backtest normalises P/E within the applicable earnings-methodology regime before pooling observations.

`SELL` means conditions are unfavourable for fresh long-horizon allocation under this model. It is not a recommendation to short NIFTY or liquidate a diversified portfolio.

## Data sources

- P/E methodology: https://www.niftyindices.com/resources/index-concepts/price-earnings-ratio
- NSE index reports: https://www.niftyindices.com/reports
- P/E series/page: https://downstox.com/nifty-pe/nifty-50
- P/E CSV: https://downstox.com/api/index-pe/nifty-50/download
- NIFTY price history fallback: https://finance.yahoo.com/quote/%5ENSEI/history/

## Troubleshooting

If an automatic run fails, open the failed workflow in **Actions** and inspect the `Refresh NIFTY valuation model` step. The most likely cause is a temporary upstream data-source response. Re-run the workflow later using **Run workflow**.

If GitHub Pages shows a 404, confirm **Settings → Pages → Source = GitHub Actions**, then manually run the workflow once.
