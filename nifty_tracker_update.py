#!/usr/bin/env python3
"""Update the NIFTY Valuation Backtest & Live Tracker.

Default source:
  Official NSE Archives daily index snapshot (ind_close_all), which contains
  NIFTY 50 close, P/E, P/B and dividend yield. Downstox/Yahoo remain fallback
  sources only if the NSE archive is temporarily unavailable.

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
from calendar import monthrange
from copy import copy
from pathlib import Path
from statistics import median
from typing import Optional, Tuple

import pandas as pd
import requests
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill

NSE_ARCHIVE_BASE = "https://nsearchives.nseindia.com/content/indices"
NSE_ARCHIVE_SOURCE = "https://www.nseindia.com/all-reports"
PE_CSV_URL = "https://downstox.com/api/index-pe/nifty-50/download"
PE_PAGE_URL = "https://downstox.com/nifty-pe/nifty-50"
MARKETS_URL = "https://downstox.com/india-markets"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI"
PRICE_PAGE_URL = "https://finance.yahoo.com/quote/%5ENSEI/history/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36"
MODEL_VERSION = "1.4"


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


def _norm_col(c: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(c).strip().lower())


def _find_col(df: pd.DataFrame, candidates) -> Optional[str]:
    normalized = {_norm_col(c): c for c in df.columns}
    for cand in candidates:
        if cand in normalized:
            return normalized[cand]
    for norm, orig in normalized.items():
        if any(cand in norm for cand in candidates):
            return orig
    return None


def fetch_nse_snapshot_for_date(s: requests.Session, d: date) -> Tuple[date, float, float, str]:
    """Fetch official NSE daily index snapshot for one trading date.

    The NSE archive's ind_close_all report includes the NIFTY 50 closing level
    and valuation fields including P/E. The archive is a static-file endpoint,
    which is materially more suitable for GitHub Actions than anti-bot protected
    HTML/API pages.
    """
    url = f"{NSE_ARCHIVE_BASE}/ind_close_all_{d:%d%m%Y}.csv"
    headers = {
        "User-Agent": UA,
        "Accept": "text/csv,text/plain,*/*",
        "Referer": "https://www.nseindia.com/",
        "Connection": "keep-alive",
    }
    r = s.get(url, headers=headers, timeout=25)
    if r.status_code == 404:
        raise FileNotFoundError(url)
    r.raise_for_status()
    body = r.text.lstrip("\ufeff \t\r\n")
    if not body or body.startswith("<"):
        raise ValueError(f"NSE archive returned non-CSV content for {d}")
    df = pd.read_csv(io.StringIO(r.text))

    name_col = _find_col(df, ["indexname", "index"])
    close_col = _find_col(df, ["closingindexvalue", "close", "closingvalue"])
    pe_col = _find_col(df, ["pe", "peratio", "priceearnings", "priceearningsratio"])
    date_col = _find_col(df, ["indexdate", "date"])
    if name_col is None or close_col is None or pe_col is None:
        raise ValueError(f"Could not identify NIFTY snapshot columns: {list(df.columns)}")

    names = df[name_col].astype(str).str.strip().str.upper().str.replace(r"\s+", " ", regex=True)
    mask = names.eq("NIFTY 50")
    if not mask.any():
        # Defensive fallback for occasional naming variations.
        mask = names.str.fullmatch(r"NIFTY\s*50")
    if not mask.any():
        raise ValueError("NIFTY 50 row not found in NSE daily index snapshot")
    row = df.loc[mask].iloc[0]
    close = float(pd.to_numeric(row[close_col], errors="raise"))
    pe = float(pd.to_numeric(row[pe_col], errors="raise"))

    out_date = d
    if date_col is not None:
        parsed = pd.to_datetime(row[date_col], errors="coerce", dayfirst=True)
        if pd.notna(parsed):
            out_date = parsed.date()
    return out_date, close, pe, url


def fetch_latest_nse_snapshot(s: requests.Session, target: date, max_lookback: int = 12) -> Tuple[date, float, float, str]:
    """Find the most recent NSE trading-day snapshot on or before target."""
    errors = []
    for i in range(max_lookback + 1):
        d = target - timedelta(days=i)
        try:
            return fetch_nse_snapshot_for_date(s, d)
        except FileNotFoundError:
            continue
        except Exception as e:
            errors.append(f"{d}: {e}")
            # Continue because a particular archive file can be delayed/corrupt.
            continue
    tail = "; ".join(errors[-3:]) if errors else "no trading-day file found"
    raise RuntimeError(f"Unable to fetch NSE index snapshot through {target}: {tail}")


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



BACKTEST_COLUMNS = [
    "Date", "Price", "PE", "Regime", "EPS", "EPS_Growth_YoY",
    "PE_Pct_Regime", "Valuation_Quintile", "Fwd_1Y", "Fwd_3Y",
    "Fwd_5Y", "Fwd_10Y", "Growth_Bucket", "PE_Pct_Expanding",
    "Valuation_Score", "Growth_Score", "Composite_Score", "Signal",
]


def latest_completed_quarter_end(d: date) -> date:
    """Return the latest calendar quarter-end on or before d."""
    q_month = ((d.month - 1) // 3 + 1) * 3
    q_end = date(d.year, q_month, monthrange(d.year, q_month)[1])
    if d >= q_end:
        return q_end
    q_month -= 3
    year = d.year
    if q_month <= 0:
        q_month += 12
        year -= 1
    return date(year, q_month, monthrange(year, q_month)[1])


def next_quarter_end(d: date) -> date:
    """Return the calendar quarter-end immediately after d."""
    month = d.month + 3
    year = d.year
    if month > 12:
        month -= 12
        year += 1
    return date(year, month, monthrange(year, month)[1])


def _regime_rank_percentile(values, x: float) -> float:
    """Average ordinal rank / N, matching the original backtest CSV."""
    vals = [float(v) for v in values if pd.notna(v)]
    if not vals:
        return float("nan")
    lt = sum(v < x for v in vals)
    eq = sum(abs(v - x) < 1e-10 for v in vals)
    return (lt + (eq + 1) / 2.0) / len(vals)


def _growth_bucket(g: float) -> Optional[str]:
    if g is None or pd.isna(g):
        return None
    if g < 0.05:
        return "<5%"
    if g <= 0.15:
        return "5-15%"
    return ">15%"


def rebuild_backtest_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Recalculate all derived quarterly backtest fields.

    The regime-normalised descriptive percentile follows the historical CSV's
    average-rank convention. The signal backtest continues to use an expanding
    no-lookahead mid-rank percentile with at least eight observations in the
    applicable earnings-methodology regime.
    """
    out = df.copy()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
    out["Price"] = pd.to_numeric(out["Price"], errors="coerce")
    out["PE"] = pd.to_numeric(out["PE"], errors="coerce")
    out = out.dropna(subset=["Date", "Price", "PE"]).sort_values("Date")
    out = out.drop_duplicates("Date", keep="last").reset_index(drop=True)

    seam = pd.Timestamp("2021-03-31")
    out["Regime"] = out["Date"].apply(lambda x: "Consolidated" if x >= seam else "Standalone")
    out["EPS"] = out["Price"] / out["PE"]

    eps_map = dict(zip(out["Date"], out["EPS"]))
    regime_map = dict(zip(out["Date"], out["Regime"]))
    growth = []
    for _, r in out.iterrows():
        prior = r["Date"] - pd.DateOffset(years=1)
        if prior in eps_map and regime_map.get(prior) == r["Regime"]:
            growth.append(float(r["EPS"] / eps_map[prior] - 1))
        else:
            growth.append(float("nan"))
    out["EPS_Growth_YoY"] = growth

    regime_pct = []
    for _, r in out.iterrows():
        vals = out.loc[out["Regime"] == r["Regime"], "PE"].tolist()
        regime_pct.append(_regime_rank_percentile(vals, float(r["PE"])))
    out["PE_Pct_Regime"] = regime_pct
    out["Valuation_Quintile"] = out["PE_Pct_Regime"].map(valuation_quintile)

    price_map = dict(zip(out["Date"], out["Price"]))
    for years, col in [(1, "Fwd_1Y"), (3, "Fwd_3Y"), (5, "Fwd_5Y"), (10, "Fwd_10Y")]:
        vals = []
        for _, r in out.iterrows():
            future = r["Date"] + pd.DateOffset(years=years)
            if future in price_map:
                vals.append(float((price_map[future] / r["Price"]) ** (1.0 / years) - 1))
            else:
                vals.append(float("nan"))
        out[col] = vals

    out["Growth_Bucket"] = out["EPS_Growth_YoY"].map(_growth_bucket)

    expanding = [float("nan")] * len(out)
    for regime in ("Standalone", "Consolidated"):
        idxs = list(out.index[out["Regime"] == regime])
        seen = []
        for idx in idxs:
            x = float(out.at[idx, "PE"])
            seen.append(x)
            if len(seen) >= 8:
                expanding[idx] = percentile_midrank(seen, x)
    out["PE_Pct_Expanding"] = expanding
    out["Valuation_Score"] = out["PE_Pct_Expanding"].map(
        lambda x: float("nan") if pd.isna(x) else 100.0 * (1.0 - float(x))
    )
    out["Growth_Score"] = out["EPS_Growth_YoY"].map(
        lambda x: float("nan") if pd.isna(x) else growth_score(float(x))
    )
    out["Composite_Score"] = 0.70 * out["Valuation_Score"] + 0.30 * out["Growth_Score"]
    out["Signal"] = out["Composite_Score"].map(
        lambda x: None if pd.isna(x) else ("BUY" if x >= 70 else ("HOLD" if x >= 40 else "SELL"))
    )
    return out[BACKTEST_COLUMNS]


def refresh_quarterly_backtest(
    s: requests.Session,
    csv_path: Path,
    asof: date,
) -> Tuple[pd.DataFrame, list[date]]:
    """Append any missing completed quarter-ends and rebuild derived fields.

    This function is safe to call every weekday. It performs no quarter-end
    network requests unless the CSV is actually missing a completed quarter.
    """
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)
    df = pd.read_csv(csv_path)
    if df.empty:
        raise ValueError("Quarterly backtest CSV is empty")
    parsed = pd.to_datetime(df["Date"], errors="coerce")
    if parsed.isna().all():
        raise ValueError("Quarterly backtest CSV has no valid dates")
    last_q = parsed.max().date()
    through = latest_completed_quarter_end(asof)
    missing = []
    q = next_quarter_end(last_q)
    while q <= through:
        missing.append(q)
        q = next_quarter_end(q)

    if not missing:
        return df, []

    additions = []
    for q_end in missing:
        snap_date, close, pe, _ = fetch_latest_nse_snapshot(s, q_end, max_lookback=12)
        additions.append({
            "Date": q_end.isoformat(),
            "Price": close,
            "PE": pe,
            "Regime": "Consolidated" if q_end >= date(2021, 3, 31) else "Standalone",
        })
        print(
            f"Quarterly backtest: added {q_end:%d-%b-%Y} "
            f"using NSE close {close:,.2f}, PE {pe:.2f}x from {snap_date:%d-%b-%Y}"
        )

    base = pd.concat([df, pd.DataFrame(additions)], ignore_index=True, sort=False)
    rebuilt = rebuild_backtest_frame(base)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    rebuilt.to_csv(csv_path, index=False, date_format="%Y-%m-%d", float_format="%.12g")
    return rebuilt, missing


def _copy_row_style(ws, source_row: int, target_row: int, max_col: int = 18):
    for c in range(1, max_col + 1):
        src = ws.cell(source_row, c)
        dst = ws.cell(target_row, c)
        if src.has_style:
            dst._style = copy(src._style)
        if src.number_format:
            dst.number_format = src.number_format
        if src.alignment:
            dst.alignment = copy(src.alignment)
        if src.protection:
            dst.protection = copy(src.protection)
    if ws.row_dimensions[source_row].height is not None:
        ws.row_dimensions[target_row].height = ws.row_dimensions[source_row].height


def sync_quarterly_sheet(wb, df: pd.DataFrame):
    """Keep the Excel Quarterly_Data sheet aligned with the canonical CSV."""
    if "Quarterly_Data" not in wb.sheetnames:
        return
    ws = wb["Quarterly_Data"]
    data_start = 5
    old_last = max(ws.max_row, data_start)
    last_row = data_start + len(df) - 1
    template_row = min(max(data_start, old_last), last_row)

    if last_row > old_last:
        for r in range(old_last + 1, last_row + 1):
            _copy_row_style(ws, template_row, r)

    # Raw inputs are written from the canonical CSV; derived fields remain formulas.
    for i, r in df.reset_index(drop=True).iterrows():
        rr = data_start + i
        dt = pd.to_datetime(r["Date"]).date()
        ws.cell(rr, 1, dt)
        ws.cell(rr, 1).number_format = "dd-mmm-yyyy"
        ws.cell(rr, 2, float(r["Price"]))
        ws.cell(rr, 3, float(r["PE"]))
        ws.cell(rr, 4, str(r["Regime"]))

    # Clear any obsolete trailing rows if the CSV was deduplicated.
    for rr in range(last_row + 1, old_last + 1):
        for cc in range(1, 19):
            ws.cell(rr, cc).value = None

    standalone_rows = [data_start + i for i, x in enumerate(df["Regime"]) if x == "Standalone"]
    consolidated_rows = [data_start + i for i, x in enumerate(df["Regime"]) if x == "Consolidated"]
    regime_bounds = {}
    if standalone_rows:
        regime_bounds["Standalone"] = (min(standalone_rows), max(standalone_rows))
    if consolidated_rows:
        regime_bounds["Consolidated"] = (min(consolidated_rows), max(consolidated_rows))

    for i, r in df.reset_index(drop=True).iterrows():
        rr = data_start + i
        regime = str(r["Regime"])
        reg_start, reg_end = regime_bounds[regime]
        ws.cell(rr, 5, f'=IFERROR(B{rr}/C{rr},"")')
        if rr - 4 >= data_start:
            ws.cell(rr, 6, f'=IF(D{rr}=D{rr-4},IFERROR(E{rr}/E{rr-4}-1,""),"")')
        else:
            ws.cell(rr, 6, None)
        ws.cell(
            rr, 7,
            f'=IFERROR((COUNTIF($C${reg_start}:$C${reg_end},"<"&C{rr})+'
            f'(COUNTIF($C${reg_start}:$C${reg_end},C{rr})+1)/2)/COUNT($C${reg_start}:$C${reg_end}),"")'
        )
        ws.cell(rr, 8, f'=IF(G{rr}="","",IF(G{rr}<=20%,"Q1 Cheapest",IF(G{rr}<=40%,"Q2",IF(G{rr}<=60%,"Q3",IF(G{rr}<=80%,"Q4","Q5 Most Expensive")))))')
        for col, offset, years in [(9, 4, 1), (10, 12, 3), (11, 20, 5), (12, 40, 10)]:
            target = rr + offset
            if target <= last_row:
                if years == 1:
                    formula = f'=IFERROR(B{target}/B{rr}-1,"")'
                else:
                    formula = f'=IFERROR((B{target}/B{rr})^(1/{years})-1,"")'
                ws.cell(rr, col, formula)
            else:
                ws.cell(rr, col, None)
        ws.cell(rr, 13, f'=IF(F{rr}="","",IF(F{rr}<5%,"<5%",IF(F{rr}<=15%,"5-15%",">15%")))')
        ws.cell(rr, 14, f'=IF(COUNT($C${reg_start}:C{rr})<8,"",(COUNTIF($C${reg_start}:C{rr},"<"&C{rr})+0.5*COUNTIF($C${reg_start}:C{rr},C{rr}))/COUNT($C${reg_start}:C{rr}))')
        ws.cell(rr, 15, f'=IF(N{rr}="","",100*(1-N{rr}))')
        ws.cell(rr, 16, f'=IF(F{rr}="","",IF(F{rr}<=0,20,IF(F{rr}<5%,20+400*F{rr},IF(F{rr}<10%,40+400*(F{rr}-5%),IF(F{rr}<15%,60+400*(F{rr}-10%),IF(F{rr}<25%,80+200*(F{rr}-15%),100))))))')
        ws.cell(rr, 17, f'=IF(OR(O{rr}="",P{rr}=""),"",70%*O{rr}+30%*P{rr})')
        ws.cell(rr, 18, f'=IF(Q{rr}="","",IF(Q{rr}>=70,"BUY",IF(Q{rr}>=40,"HOLD","SELL")))')

    # Extend every summary-sheet reference to the current Quarterly_Data last row.
    pat = re.compile(r'(Quarterly_Data!\$[A-R]\$5:\$[A-R]\$)\d+')
    for sheet_name in ("Backtest", "Growth_Matrix", "Signal_Backtest"):
        if sheet_name not in wb.sheetnames:
            continue
        for row in wb[sheet_name].iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    cell.value = pat.sub(lambda m: f"{m.group(1)}{last_row}", cell.value)

    # Future-proof the consolidated-era P/E history range beyond the current row count.
    if "Dashboard" in wb.sheetnames:
        dash = wb["Dashboard"]
        dash["B9"] = '=IFERROR((COUNTIF(PE_History_Post2021!$B$5:$B$500,"<"&B7)+0.5*COUNTIF(PE_History_Post2021!$B$5:$B$500,B7))/COUNT(PE_History_Post2021!$B$5:$B$500),"")'
        dash["B10"] = '=MEDIAN(PE_History_Post2021!$B$5:$B$500)'

    if "Methodology" in wb.sheetnames:
        through = pd.to_datetime(df["Date"]).max().date()
        wb["Methodology"]["B10"] = (
            f"Quarter-end observations from Mar-1999 to {through:%b-%Y}. "
            "This reduces, but does not eliminate, overlapping forward-return windows."
        )

def _linear_quantile(values, q: float) -> float:
    """Simple linear-interpolated quantile used for signal-boundary estimates."""
    vals = sorted(
        float(v) for v in values
        if v is not None and not (isinstance(v, float) and math.isnan(v))
    )
    if not vals:
        return float("nan")
    q = max(0.0, min(1.0, float(q)))
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    w = pos - lo
    return vals[lo] * (1 - w) + vals[hi] * w


def compute_signal_boundaries(pe_values, growth_score_value: float, current_eps: float, current_close: float):
    """Approximate P/E/NIFTY levels where the composite score crosses 70 and 40.

    Implied EPS and the earnings-growth score are held constant.
    """
    wv, wg = 0.70, 0.30

    def one(score_threshold: float):
        req_valuation_score = (score_threshold - wg * growth_score_value) / wv
        req_valuation_score = max(0.0, min(100.0, req_valuation_score))
        target_pct = 1.0 - req_valuation_score / 100.0
        pe = _linear_quantile(pe_values, target_pct)
        nifty = current_eps * pe
        move = nifty / current_close - 1.0 if current_close else float("nan")
        return {
            "score_threshold": score_threshold,
            "valuation_score_required": req_valuation_score,
            "pe_percentile": target_pct,
            "pe": pe,
            "nifty_level": nifty,
            "move_from_current": move,
        }

    return {
        "buy_hold": one(70.0),
        "hold_sell": one(40.0),
        "assumption": "Current implied EPS and earnings-growth score held constant",
        "note": "Approximate because regime-relative P/E percentiles are based on a discrete historical distribution.",
    }


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
            ws.cell(rr, 4, NSE_ARCHIVE_SOURCE)
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
    ws.cell(rr, 4, NSE_ARCHIVE_SOURCE)


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
    signal_boundaries = compute_signal_boundaries(
        pe_values, metrics["growth_score"], metrics["eps"], current_close
    )
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
        "signal_boundaries": {
            "buy_hold": {k: _json_num(v, 8) if isinstance(v, (int, float)) else v for k, v in signal_boundaries["buy_hold"].items()},
            "hold_sell": {k: _json_num(v, 8) if isinstance(v, (int, float)) else v for k, v in signal_boundaries["hold_sell"].items()},
            "assumption": signal_boundaries["assumption"],
            "note": signal_boundaries["note"],
        },
        "weights": {"valuation": 0.70, "earnings_growth": 0.30},
        "sources": {"pe": NSE_ARCHIVE_SOURCE, "price": NSE_ARCHIVE_SOURCE},
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
    dates = pd.to_datetime(df.get("Date"), errors="coerce") if "Date" in df.columns else pd.Series(dtype="datetime64[ns]")
    through = None if dates.empty or dates.isna().all() else dates.max().date().isoformat()
    out_path.write_text(
        json.dumps({"through": through, "observation_count": int(len(df)), "rows": rows}, indent=2),
        encoding="utf-8",
    )


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
    target_date = manual_date or date.today()
    nse_error = None
    try:
        snap_date, snap_close, snap_pe, snap_url = fetch_latest_nse_snapshot(s, target_date)
    except Exception as e:
        nse_error = e
        snap_date = target_date
        snap_close = None
        snap_pe = None
        snap_url = NSE_ARCHIVE_SOURCE

    # Prefer official NSE archive data. Manual CLI inputs override individual fields.
    if args.pe is not None:
        current_pe = float(args.pe)
        pe_date = manual_date or snap_date
    elif snap_pe is not None:
        current_pe = float(snap_pe)
        pe_date = snap_date
    else:
        # Legacy fallback retained for resilience, although some cloud IP ranges
        # can be blocked by Downstox.
        try:
            pe_date, current_pe = fetch_current_pe_page(s)
        except Exception as e_page:
            raise RuntimeError(f"Unable to fetch P/E. NSE archive error: {nse_error}; fallback error: {e_page}")

    if args.close is not None:
        current_close = float(args.close)
        price_date = manual_date or snap_date
    elif snap_close is not None:
        current_close = float(snap_close)
        price_date = snap_date
    else:
        try:
            price_date, current_close = fetch_latest_close(s, pe_date)
        except Exception as e_price:
            raise RuntimeError(f"Unable to fetch NIFTY close. NSE archive error: {nse_error}; fallback error: {e_price}")

    asof = manual_date or min(pe_date, price_date)

    # Self-maintaining quarterly backtest: this is checked on every weekday run,
    # but NSE is queried for quarter-end data only when a completed quarter is missing.
    quarterly_df, added_quarters = refresh_quarterly_backtest(s, backtest_csv, asof)

    wb = load_workbook(p)
    sync_quarterly_sheet(wb, quarterly_df)
    pe_ws = wb["PE_History_Post2021"]
    update_pe_sheet(pe_ws, pe_hist, pe_date, current_pe)

    rows = month_rows_from_workbook(pe_ws)

    # For the earnings-growth modifier, compare against the nearest trading day
    # on or before the same calendar date one year earlier. This keeps both the
    # NIFTY level and P/E from the same official NSE daily snapshot.
    try:
        prior_target = asof.replace(year=asof.year - 1)
    except ValueError:
        # 29-Feb -> 28-Feb in the prior non-leap year.
        prior_target = asof.replace(year=asof.year - 1, day=28)
    try:
        prior_price_date, prior_close, prior_pe, _ = fetch_latest_nse_snapshot(s, prior_target)
    except Exception:
        # Workbook fallback keeps the model usable during a temporary NSE archive outage.
        dash = wb["Dashboard"]
        prior_price_date = dash["B12"].value
        if isinstance(prior_price_date, datetime):
            prior_price_date = prior_price_date.date()
        prior_close = float(dash["B13"].value)
        prior_pe = float(dash["B14"].value)

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
        snap_url if 'snap_url' in locals() else NSE_ARCHIVE_SOURCE,
        snap_url if 'snap_url' in locals() else NSE_ARCHIVE_SOURCE,
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
    if added_quarters:
        print("Quarterly backtest refreshed through: " + added_quarters[-1].strftime("%d-%b-%Y"))
    else:
        print("Quarterly backtest: already current for the latest completed quarter")
    print(f"Web dashboard data: {web_dir}")


if __name__ == "__main__":
    main()
