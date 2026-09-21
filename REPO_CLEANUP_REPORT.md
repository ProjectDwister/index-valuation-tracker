# Repository cleanup applied — 21 Sep 2026

This cleanup intentionally leaves the published scoring formula and historical backtest methodology unchanged.

## Applied

- Tightened dashboard eligibility to:
  - current P/E available
  - live composite score available
  - >= 8 post-2021 monthly P/E observations
  - >= 32 P/E-bearing quarter-end observations
  - >= 16 matured 3-year forward-return observations
- Current displayed universe reduced from 83 to 51 statistically more useful indices.
- NIFTY 50 continues to use the canonical 1999-onward backtest (110 P/E-bearing quarters).
- Removed obsolete duplicate webpage files from the repository root and `docs/data/`.
- Disabled the legacy NIFTY-only email alert engine by default; multi-index consolidated alerts are authoritative.
- Removed the superseded `docs/data/threshold_alert_state.json` state file.
- Externalised NSE market holidays to `docs/data/nse_market_holidays.json` for annual maintenance.
- Market-status logic now fails conservatively to red if the current year is not covered by the holiday calendar.
- Updated repository documentation and old repository naming.
- Workflow housekeeping now automatically removes obsolete duplicate site files if they reappear.
- Existing per-index downloads were pruned to match the 51-index eligible universe.

## Validation completed

- Python syntax: passed for both updater scripts.
- JavaScript syntax: passed.
- JSON parsing: passed for all `docs/data/*.json` files.
- Repository workflow YAML: parsed successfully.
- Remaining Excel files: 53 opened successfully; no obvious `#REF!`, `#DIV/0!`, `#VALUE!` or `#NAME?` tokens found.
- Catalog / latest / backtests / history / Excel-download universe: all aligned to 51 indices.

## Deliberately not changed

The current historical valuation-bucket backtest still uses full-regime descriptive P/E percentiles rather than strict expanding-history/no-lookahead percentiles. This should be tested separately before changing published backtest numbers.
