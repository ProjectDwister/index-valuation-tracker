#!/usr/bin/env python3
"""Update the NIFTY Valuation Backtest & Live Tracker.

Default sources:
  P/E: Downstox NIFTY 50 PE dataset/page (underlying source NSE Indices)
  Price: Yahoo Finance chart endpoint (^NSEI), with Downstox India Markets fallback

Besides updating the Excel workbook, the script writes static web data for the
GitHub Pages dashboard under docs/data/ and copies the current workbook under
_docs/downloads/.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import shutil
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Optional, Tuple

import pandas as pd
import requests
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill

PE_CSV_URL = "https://downstox.com/api/index-pe/nifty-50/download"
PE_PAGE_URL = "https://downstox.com/nifty-pe/nifty-50"
MARKETS_URL = "https://downstox.com/india-markets"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI"
PRICE_PAGE_URL = "https://finance.yahoo.com/quote/%5ENSEI/history/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36"
MODEL_VERSION = "1.1"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    return s


def _normalise_pe_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Return DataFrame with columns date, pe from a reasonably named CSV."""
    cols = {str(c).strip().lower(): c for c in df.columns}
    date_col = next((orig for low, orig in cols.items() if "date" in low), None)
    pe_col = None
    for low, orig in cols.items():
        canonical = re.sub(r"[^a-z]", "", low)
        if canonical in {"pe", "peratio", "priceearnings", "priceearningsratio"}:
            pe_col = orig
            break
    if pe_col is None:
        pe_col = next((orig for low, orig in cols.items() if "p/e" in low or low.strip() == "pe"), None)
    if date_col is None or pe_col is None:
        raise ValueError(f"Could not identify date/PE columns. Columns were: {list(df.columns)}")
    out = df[[date_col, pe_col]].copy()
    out.columns = ["date", "pe"]
    out["date"] = pd.to_datetime(out["date"], errors="coerce", dayfirst=True)
    out["pe"] = pd.to_numeric(out["pe"], errors="coerce")
    return out.dropna().sort_values("date").drop_duplicates("date", keep="last")


def fetch_pe_history(s: requests.Session) -> pd.DataFrame:
    r = s.get(PE_CSV_URL, timeout=25)
    r.raise_for_status()
    return _normalise_pe_frame(pd.read_csv(io.StringIO(r.text)))


def fetch_current_pe_page(s: requests.Session) -> Tuple[date, float]:
    r = s.get(PE_PAGE_URL, timeout=25)
    r.raise_for_status()
    txt = re.sub(r"\s+", " ", r.text)
    m = re.search(
        r"NIFTY\s*50\s*PE ratio is\s*([0-9]+(?:\.[0-9]+)?)\s*as of\s*([0-9]{1,2})\s+([A-Za-z]+)\s+([0-9]{4})",
        txt,
        re.I,
    )
    if not m:
        m = re.search(
            r"PE ratio(?:\s+today)?[^0-9]{0,80}([0-9]{1,2}\.[0-9]{1,3})[^0-9]{0,80}([0-9]{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+([0-9]{4})",
            txt,
            re.I,
        )
    if not m:
        raise ValueError("Could not parse current NIFTY P/E from Downstox page")
    pe = float(m.group(1))
    day, mon, year = int(m.group(2)), m.group(3), int(m.group(4))
    mon = "Sep" if mon.lower().startswith("sept") else mon[:3].title()
    dt = datetime.strptime(f"{day} {mon} {year}", "%d %b %Y").date()
    return dt, pe


def fetch_yahoo_daily(s: requests.Session, start: date, end: date) -> pd.DataFrame:
    # Yahoo period2 is exclusive.
    p1 = int(datetime.combine(start, datetime.min.time()).timestamp())
    p2 = int(datetime.combine(end + timedelta(days=1), datetime.min.time()).timestamp())
    r = s.get(
        YAHOO_URL,
        params={"period1": p1, "period2": p2, "interval": "1d", "events": "history"},
        timeout=25,
    )
    r.raise_for_status()
    js = r.json()
    result = js.get("chart", {}).get("result")
    if not result:
        raise ValueError(f"Yahoo returned no result: {js.get('chart', {}).get('error')}")
    z = result[0]
    ts = z.get("timestamp") or []
    close = ((z.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    rows = []
    for t, c in zip(ts, close):
        if c is None:
            continue
        rows.append((datetime.fromtimestamp(t).date(), float(c)))
    if not rows:
        raise ValueError("Yahoo returned no usable NIFTY closes")
    return pd.DataFrame(rows, columns=["date", "close"]).sort_values("date")


def fetch_latest_close(s: requests.Session, asof: date) -> Tuple[date, float]:
    try:
        d = fetch_yahoo_daily(s, asof - timedelta(days=10), asof)
        x = d.iloc[-1]
        return x["date"], float(x["close"])
    except Exception as yahoo_err:
        r = s.get(MARKETS_URL, timeout=25)
        r.raise_for_status()
        txt = re.sub(r"\s+", " ", r.text)
        pos = txt.upper().find("NIFTY 50")
        window = txt[pos : pos + 2500] if pos >= 0 else txt
        nums = re.findall(r"\b([0-9]{2},[0-9]{3}\.[0-9]{1,2})\b", window)
        if not nums:
            raise RuntimeError(f"Price sources failed. Yahoo: {yahoo_err}")
        return asof, float(nums[0].replace(",", ""))


def fetch_month_last_close(s: requests.Session, year: int, month: int) -> Tuple[date, float]:
    start = date(year, month, 1)
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    d = fetch_yahoo_daily(s, start, next_month - timedelta(days=1))
    x = d.iloc[-1]
    return x["date"], float(x["close"])


def percentile_midrank(values, x: float) -> float:
    vals = [
        float(v)
        for v in values
        if v is not None and not (isinstance(v, float) and math.isnan(v))
    ]
    if not vals:
        return float("nan")
    lt = sum(v < x for v in vals)
    eq = sum(abs(v - x) < 1e-10 for v in vals)
    return (lt + 0.5 * eq) / len(vals)


def growth_score(g: float) -> float:
    if g <= 0:
        return 20.0
    if g < 0.05:
        return 20 + 400 * g
    if g < 0.10:
        return 40 + 400 * (g - 0.05)
    if g < 0.15:
        return 60 + 400 * (g - 0.10)
    if g < 0.25:
        return 80 + 200 * (g - 0.15)
    return 100.0


def compute_signal(current_close, current_pe, prior_close, prior_pe, pe_values):
    eps = current_close / current_pe
    prior_eps = prior_close / prior_pe
    growth = eps / prior_eps - 1
    pct = percentile_midrank(pe_values, current_pe)
    valuation_score = 100 * (1 - pct)
    gs = growth_score(growth)
    composite = 0.70 * valuation_score + 0.30 * gs
    signal = "BUY" if composite >= 70 else ("HOLD" if composite >= 40 else "SELL")
    return dict(
        eps=eps,
        prior_eps=prior_eps,
        growth=growth,
        pct=pct,
        valuation_score=valuation_score,
        growth_score=gs,
        composite=composite,
        signal=signal,
    )


def valuation_quintile(pct: float) -> str:
    if pct <= 0.20:
        return "Q1 Cheapest"
    if pct <= 0.40:
        return "Q2"
    if pct <= 0.60:
        return "Q3"
    if pct <= 0.80:
        return "Q4"
    return "Q5 Most Expensive"


def month_rows_from_workbook(ws):
    rows = []
    for r in range(5, ws.max_row + 1):
        dt = ws.cell(r, 1).value
        pe = ws.cell(r, 2).value
        if not dt or pe in (None, ""):
            continue
        if isinstance(dt, datetime):
            dt = dt.date()
        if isinstance(dt, date):
            rows.append((r, dt, float(pe)))
    return rows


def update_pe_sheet(ws, pe_hist: Optional[pd.DataFrame], current_date: date, current_pe: float):
    # If full history downloaded, refresh Apr-2021 onward. Otherwise update/append current month.
    if pe_hist is not None and len(pe_hist):
        h = pe_hist[pe_hist["date"] >= pd.Timestamp("2021-04-01")].copy()
        for rr in range(5, max(ws.max_row, 5) + 1):
            for cc in range(1, 5):
                ws.cell(rr, cc).value = None
        rr = 5
        for _, x in h.iterrows():
            dt = x["date"].date()
            pe = float(x["pe"])
            ws.cell(rr, 1, dt)
            ws.cell(rr, 1).number_format = "dd-mmm-yyyy"
            ws.cell(rr, 2, pe)
            ws.cell(rr, 2).number_format = "0.00x"
            ws.cell(rr, 3, "Consolidated")
            ws.cell(rr, 4, PE_PAGE_URL)
            rr += 1
        return
    rows = month_rows_from_workbook(ws)
    match = [x for x in rows if x[1].year == current_date.year and x[1].month == current_date.month]
    rr = match[-1][0] if match else max(ws.max_row + 1, 5)
    ws.cell(rr, 1, current_date)
    ws.cell(rr, 1).number_format = "dd-mmm-yyyy"
    ws.cell(rr, 2, current_pe)
    ws.cell(rr, 2).number_format = "0.00x"
    ws.cell(rr, 3, "Consolidated")
    ws.cell(rr, 4, PE_PAGE_URL)


def style_log_row(ws, r):
    for c in range(1, 13):
        ws.cell(r, c).font = Font(color="000000")
    ws.cell(r, 1).number_format = "dd-mmm-yyyy"
    ws.cell(r, 2).number_format = "#,##0.00;[Red](#,##0.00);-"
    ws.cell(r, 3).number_format = "0.00x"
    ws.cell(r, 4).number_format = "0.0%"
    ws.cell(r, 5).number_format = "#,##0.00"
    ws.cell(r, 6).number_format = "0.0%"
    for c in [7, 8, 9]:
        ws.cell(r, c).number_format = "0.0"
    sig = ws.cell(r, 10).value
    fill = {"BUY": "E2F0D9", "HOLD": "FFF2CC", "SELL": "F4CCCC"}.get(sig, "FFFFFF")
    ws.cell(r, 10).fill = PatternFill("solid", fgColor=fill)
    ws.cell(r, 10).font = Font(bold=True)


def _json_num(x, digits=6):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return round(float(x), digits)


def write_history_csv(log_ws, out_path: Path, seed_record=None):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "date", "nifty_close", "pe", "pe_percentile", "implied_eps", "yoy_eps_growth",
        "valuation_score", "growth_score", "composite_score", "signal"
    ]
    records = []
    for rr in range(5, log_ws.max_row + 1):
        dt = log_ws.cell(rr, 1).value
        if isinstance(dt, datetime):
            dt = dt.date()
        if not isinstance(dt, date):
            continue
        vals = [log_ws.cell(rr, c).value for c in range(2, 11)]
        if any(isinstance(v, str) and v.startswith("=") for v in vals):
            continue
        if vals[-1] not in {"BUY", "HOLD", "SELL"}:
            continue
        records.append([dt.isoformat(), *vals])
    if seed_record is not None and not any(r[0] == seed_record[0] for r in records):
        records.append(seed_record)
    records.sort(key=lambda r: r[0])
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(records)


def write_latest_json(
    out_path: Path,
    asof: date,
    current_close: float,
    current_pe: float,
    prior_price_date: date,
    prior_close: float,
    prior_pe: float,
    pe_values,
    metrics,
):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    era_median = median([float(v) for v in pe_values])
    payload = {
        "model_version": MODEL_VERSION,
        "as_of": asof.isoformat(),
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "signal": metrics["signal"],
        "composite_score": _json_num(metrics["composite"], 2),
        "valuation_score": _json_num(metrics["valuation_score"], 2),
        "growth_score": _json_num(metrics["growth_score"], 2),
        "nifty_close": _json_num(current_close, 2),
        "pe": _json_num(current_pe, 2),
        "earnings_yield": _json_num(1 / current_pe, 8),
        "pe_percentile": _json_num(metrics["pct"], 8),
        "valuation_quintile": valuation_quintile(metrics["pct"]),
        "era_median_pe": _json_num(era_median, 2),
        "implied_eps": _json_num(metrics["eps"], 2),
        "yoy_eps_growth": _json_num(metrics["growth"], 8),
        "prior_year": {
            "date": prior_price_date.isoformat(),
            "nifty_close": _json_num(prior_close, 2),
            "pe": _json_num(prior_pe, 2),
            "implied_eps": _json_num(metrics["prior_eps"], 2),
        },
        "thresholds": {"buy": 70, "hold": 40},
        "weights": {"valuation": 0.70, "earnings_growth": 0.30},
        "sources": {"pe": PE_PAGE_URL, "price": PRICE_PAGE_URL},
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_backtest_summary(csv_path: Path, out_path: Path):
    if not csv_path.exists():
        return
    df = pd.read_csv(csv_path)
    order = ["Q1 Cheapest", "Q2", "Q3", "Q4", "Q5 Most Expensive"]
    rows = []
    for q in order:
        g = df[df["Valuation_Quintile"] == q]
        def med(col):
            x = pd.to_numeric(g[col], errors="coerce").dropna()
            return None if x.empty else float(x.median())
        def loss(col):
            x = pd.to_numeric(g[col], errors="coerce").dropna()
            return None if x.empty else float((x < 0).mean())
        def n(col):
            return int(pd.to_numeric(g[col], errors="coerce").notna().sum())
        rows.append({
            "quintile": q,
            "median_1y": _json_num(med("Fwd_1Y"), 8),
            "median_3y": _json_num(med("Fwd_3Y"), 8),
            "median_5y": _json_num(med("Fwd_5Y"), 8),
            "median_10y": _json_num(med("Fwd_10Y"), 8),
            "loss_3y": _json_num(loss("Fwd_3Y"), 8),
            "n_3y": n("Fwd_3Y"),
        })
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"rows": rows}, indent=2), encoding="utf-8")


def export_web_files(
    wb,
    workbook_path: Path,
    web_dir: Path,
    backtest_csv: Path,
    asof: date,
    current_close: float,
    current_pe: float,
    prior_price_date: date,
    prior_close: float,
    prior_pe: float,
    pe_values,
    metrics,
):
    data_dir = web_dir / "data"
    write_latest_json(
        data_dir / "latest.json", asof, current_close, current_pe,
        prior_price_date, prior_close, prior_pe, pe_values, metrics,
    )
    seed = [asof.isoformat(), current_close, current_pe, metrics["pct"], metrics["eps"], metrics["growth"], metrics["valuation_score"], metrics["growth_score"], metrics["composite"], metrics["signal"]]
    write_history_csv(wb["Daily_Log"], data_dir / "history.csv", seed_record=seed)
    write_backtest_summary(backtest_csv, data_dir / "backtest_summary.json")
    downloads = web_dir / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    shutil.copy2(workbook_path, downloads / workbook_path.name)


def metrics_from_workbook(wb):
    dash = wb["Dashboard"]
    asof = dash["B5"].value
    prior_date = dash["B12"].value
    if isinstance(asof, datetime):
        asof = asof.date()
    if isinstance(prior_date, datetime):
        prior_date = prior_date.date()
    current_close = float(dash["B6"].value)
    current_pe = float(dash["B7"].value)
    prior_close = float(dash["B13"].value)
    prior_pe = float(dash["B14"].value)
    pe_values = [pe for _, _, pe in month_rows_from_workbook(wb["PE_History_Post2021"])]
    if not any(abs(x - current_pe) < 1e-10 for x in pe_values):
        pe_values.append(current_pe)
    metrics = compute_signal(current_close, current_pe, prior_close, prior_pe, pe_values)
    return asof, current_close, current_pe, prior_date, prior_close, prior_pe, pe_values, metrics


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workbook", default="NIFTY_Valuation_Backtest_Live_Tracker.xlsx")
    ap.add_argument("--output", default=None, help="Optional output path; default overwrites workbook")
    ap.add_argument("--asof", default=None, help="Manual as-of YYYY-MM-DD")
    ap.add_argument("--pe", type=float, default=None, help="Manual current P/E (skips PE web fetch)")
    ap.add_argument("--close", type=float, default=None, help="Manual current NIFTY close (skips price web fetch)")
    ap.add_argument("--web-dir", default="docs", help="Static dashboard output directory")
    ap.add_argument("--backtest-csv", default="nifty_quarterly_backtest.csv")
    ap.add_argument(
        "--export-web-only",
        action="store_true",
        help="Do not fetch market data; export the current workbook state to docs/data",
    )
    args = ap.parse_args()

    p = Path(args.workbook).resolve()
    out = Path(args.output).resolve() if args.output else p
    web_dir = Path(args.web_dir).resolve()
    backtest_csv = Path(args.backtest_csv).resolve()
    if not p.exists():
        raise FileNotFoundError(p)

    if args.export_web_only:
        wb = load_workbook(p)
        vals = metrics_from_workbook(wb)
        export_web_files(wb, p, web_dir, backtest_csv, *vals)
        print(f"Exported web dashboard data from workbook: {web_dir}")
        return

    s = _session()
    manual_date = datetime.strptime(args.asof, "%Y-%m-%d").date() if args.asof else None

    pe_hist = None
    if args.pe is not None:
        current_pe = float(args.pe)
        pe_date = manual_date or date.today()
    else:
        try:
            pe_hist = fetch_pe_history(s)
            x = pe_hist.iloc[-1]
            pe_date = x["date"].date()
            current_pe = float(x["pe"])
        except Exception as e_csv:
            try:
                pe_date, current_pe = fetch_current_pe_page(s)
            except Exception as e_page:
                raise RuntimeError(f"Unable to fetch P/E. CSV error: {e_csv}; page error: {e_page}")
    asof = manual_date or pe_date

    if args.close is not None:
        price_date = asof
        current_close = float(args.close)
    else:
        price_date, current_close = fetch_latest_close(s, asof)
        asof = min(pe_date, price_date)

    wb = load_workbook(p)
    pe_ws = wb["PE_History_Post2021"]
    update_pe_sheet(pe_ws, pe_hist, pe_date, current_pe)

    rows = month_rows_from_workbook(pe_ws)
    candidates = [(dt, pe) for _, dt, pe in rows if dt.year == asof.year - 1 and dt.month == asof.month]
    if not candidates:
        raise RuntimeError(f"No prior-year P/E history for {asof.year - 1}-{asof.month:02d}")
    _, prior_pe = candidates[-1]
    try:
        prior_price_date, prior_close = fetch_month_last_close(s, asof.year - 1, asof.month)
    except Exception:
        dash = wb["Dashboard"]
        if asof.year == 2026 and asof.month == 9:
            prior_price_date = dash["B12"].value
            if isinstance(prior_price_date, datetime):
                prior_price_date = prior_price_date.date()
            prior_close = float(dash["B13"].value)
        else:
            raise

    pe_values = [pe for _, _, pe in rows]
    if not any(abs(x - current_pe) < 1e-10 for x in pe_values):
        pe_values.append(current_pe)
    metrics = compute_signal(current_close, current_pe, prior_close, prior_pe, pe_values)

    dash = wb["Dashboard"]
    dash["B5"] = asof
    dash["B5"].number_format = "dd-mmm-yyyy"
    dash["B6"] = current_close
    dash["B7"] = current_pe
    dash["B12"] = prior_price_date
    dash["B12"].number_format = "dd-mmm-yyyy"
    dash["B13"] = prior_close
    dash["B14"] = prior_pe

    log = wb["Daily_Log"]
    target = None
    for rr in range(5, log.max_row + 1):
        v = log.cell(rr, 1).value
        if isinstance(v, datetime):
            v = v.date()
        if v == asof:
            target = rr
            break
    if target is None:
        target = max(log.max_row + 1, 5)
    vals = [
        asof,
        current_close,
        current_pe,
        metrics["pct"],
        metrics["eps"],
        metrics["growth"],
        metrics["valuation_score"],
        metrics["growth_score"],
        metrics["composite"],
        metrics["signal"],
        PE_PAGE_URL,
        PRICE_PAGE_URL,
    ]
    for c, v in enumerate(vals, 1):
        log.cell(target, c, v)
    style_log_row(log, target)

    try:
        wb.calculation.fullCalcOnLoad = True
        wb.calculation.forceFullCalc = True
        wb.calculation.calcMode = "auto"
    except Exception:
        pass
    wb.save(out)

    # Export web data after saving so the downloadable workbook matches the dashboard.
    export_web_files(
        wb,
        out,
        web_dir,
        backtest_csv,
        asof,
        current_close,
        current_pe,
        prior_price_date,
        prior_close,
        prior_pe,
        pe_values,
        metrics,
    )

    print(f"Updated: {out}")
    print(f"As of: {asof:%d-%b-%Y} | NIFTY {current_close:,.2f} | PE {current_pe:.2f}x")
    print(f"PE percentile: {metrics['pct']:.1%} | approx EPS growth: {metrics['growth']:.1%}")
    print(f"Composite score: {metrics['composite']:.1f} | Signal: {metrics['signal']}")
    print(f"Web dashboard data: {web_dir}")


if __name__ == "__main__":
    main()
