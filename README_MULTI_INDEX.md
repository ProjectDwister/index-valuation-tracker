# Multi-index valuation engine

`multi_index_tracker.py` extends the canonical NIFTY 50 tracker to other eligible NSE equity indices.

## Per-index calculations

Each index uses its own:
- index level
- P/E
- implied EPS = index level / P/E
- YoY implied EPS growth
- post-2021 P/E percentile
- valuation score
- growth score
- composite score
- BUY / HOLD / SELL thresholds
- historical forward returns

No other index is scored using NIFTY 50 P/E or EPS.

## Useful-history filter

To appear in the selector and heatmap an index must have:
- current P/E available
- live composite score available
- at least 8 comparable post-2021 monthly P/E observations
- at least 32 P/E-bearing quarter-end observations
- at least 16 matured 3-year forward-return observations

This intentionally removes short-history thematic indices whose backtests are too sparse to be useful.

Raw history remains in `multi_index_monthly.csv` and `multi_index_quarterly.csv`, allowing an index to qualify automatically later.

## NIFTY 50 exception

NIFTY 50 keeps the canonical 1999-onward backtest from `nifty_quarterly_backtest.csv`. Other indices use their eligible history from the common multi-index archive.

## Historical percentile note

The current forward-return tables use regime-normalised descriptive historical percentiles. They are not yet converted to strict expanding-history/no-lookahead percentiles; that methodology is being treated as a separate research decision so current published backtest numbers are not silently changed.
