# Index Composition & Weightage

The tracker now has a **Composition** tab for every eligible index.

## Official source

Constituent weights are refreshed from the NSE Indices monthly report:

https://www.niftyindices.com/reports/monthly-reports

Report: **Indices Market Capitalisation & Weightage**

The updater searches the latest available monthly ZIP (`indices_data{Mon}{YYYY}.zip`) and parses official constituent weights. For tracked indices not covered by that ZIP, it discovers the **Index Constituent** CSV on each index's official NSE Indices page and imports the stock list. It writes one JSON file per tracked index under:

`docs/data/composition/`

No synthetic weights are created. The CSVs can list constituent names, symbols and industries without weights. Those indices show membership and a count by industry, with stock weights marked unavailable. Weighted snapshots keep the report's month-end date; a CSV without an embedded effective date shows its retrieval date as such.

Official example: [NIFTY 100 constituents](https://www.niftyindices.com/indices/equity/broad-based-indices/nifty-100).

## Dashboard

The **Composition** tab provides:

- constituent names and any officially published weights
- symbols where provided by NSE
- sector / industry where provided by NSE
- top-10 concentration and weight coverage where available
- Stocks / Sectors views; the latter uses stock counts when weights are absent
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

If an official download is temporarily unavailable, the composition updater keeps the most recent previously stored **official** file and does not replace it with guesses. The `status.json` file records missing indices and source errors.
