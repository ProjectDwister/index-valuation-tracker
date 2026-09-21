# Dynamic per-index Excel downloads

Replace these four files in the repository:

- `multi_index_tracker.py`
- `docs/app.js`
- `docs/index.html`
- `.github/workflows/nifty-tracker.yml`

Then run the GitHub Action once.

The multi-index refresh will create one workbook per eligible dashboard index under:

`docs/downloads/indices/<index-slug>.xlsx`

Examples:
- `docs/downloads/indices/nifty-50.xlsx`
- `docs/downloads/indices/nifty-bank.xlsx`
- `docs/downloads/indices/nifty-it.xlsx`

The top-right download button now changes with the selected index and points to that index's workbook.

Each workbook contains:
1. Overview — current valuation, score, signal, thresholds and forward-return summary
2. Monthly PE History — own-index close, P/E, formula-derived implied EPS and earnings yield
3. Quarterly Backtest — own-index quarter-end history and formula-derived backtest fields
4. Score History — own-index score and signal history with formula-reconstructed scores
5. Sources & Methodology

NIFTY 50 continues to use the canonical 1999-onward quarter-end history for its backtest. Other indices use their own eligible multi-index history.
