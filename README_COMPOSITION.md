# Index Composition & Weightage

The tracker now has a **Composition** tab for every eligible index.

## Official source

Composition is refreshed from the NSE Indices monthly report:

https://www.niftyindices.com/reports/monthly-reports

Report: **Indices Market Capitalisation & Weightage**

The updater searches the latest available monthly ZIP (`indices_data{Mon}{YYYY}.zip`), parses official constituent weights, and writes one JSON file per tracked index under:

`docs/data/composition/`

No synthetic weights are created. If an official index table cannot be parsed, that index is marked unavailable rather than estimated.

## Dashboard

The **Composition** tab provides:

- constituent names and weights
- symbols where provided by NSE
- sector / industry where provided by NSE
- top-10 concentration
- weight coverage
- Stocks / Sectors views
- constituent search

The Heatmap also shows **Top 10** concentration when composition data is available.

## Excel

After the multi-index workbooks are generated, `composition_tracker.py` adds/replaces a **Composition** worksheet in every per-index workbook.

## Refresh order

The GitHub Action runs:

1. NIFTY 50 canonical updater
2. multi-index valuation/backtest updater
3. official composition/weightage updater
4. repository housekeeping / commit / Pages deployment

If the NSE monthly ZIP is temporarily unavailable, the composition updater keeps the most recent previously stored **official** files and does not replace them with guesses.
