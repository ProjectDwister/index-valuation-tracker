# Dynamic per-index Excel downloads

Every eligible dashboard index receives its own workbook under:

`docs/downloads/indices/<index-slug>.xlsx`

The top-right Excel button automatically points to the selected index's workbook.

Each workbook contains:
1. Overview - current valuation, score, signal, thresholds and forward-return summary
2. Monthly PE History - own-index close, P/E, implied EPS and earnings yield
3. Quarterly Backtest - own-index quarter-end history and forward-return fields
4. Score History - own-index score and signal history
5. Sources & Methodology

NIFTY 50 uses the canonical 1999-onward quarter-end backtest. Other indices use their own qualifying multi-index history.

The updater clears the per-index download directory before regeneration, so workbooks for indices that no longer pass the useful-history filter are automatically removed.

## Composition worksheet

After the standard per-index workbook is generated, `composition_tracker.py` adds a **Composition** worksheet using the official NSE Indices monthly report or the index's official constituent CSV. The sheet contains rank, company, symbol, sector/industry where available, and an official weight where published. Missing weights are left blank.
