CANONICAL NIFTY 50 MODEL
========================

NIFTY 50 remains the default index and retains the original long-history model.

Canonical files:
  NIFTY_Valuation_Backtest_Live_Tracker.xlsx
  nifty_quarterly_backtest.csv
  nifty_tracker_update.py

The canonical NIFTY 50 updater refreshes the workbook and fallback NIFTY-only web data.
The multi-index engine is authoritative for website index selection, dynamic per-index Excel downloads and email alerts.

Legacy NIFTY-only email alerts are disabled by default. They can be re-enabled only by explicitly setting:
  ENABLE_LEGACY_NIFTY_ALERTS=true

For current setup and methodology, read README.md.
