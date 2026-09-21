#!/usr/bin/env python3
"""Build multi-index NIFTY valuation/backtest data for GitHub Pages.

This script is additive to the existing NIFTY 50 tracker. It uses the official
NSE daily multi-index archive file (ind_close_all_DDMMYYYY.csv), so one network
request gives close and valuation fields for many NIFTY indices at once.

Outputs under docs/data/:
  multi_index_catalog.json
  multi_index_latest.json
  multi_index_backtests.json
  multi_index_history.csv

Persistent canonical data at repository root:
  multi_index_quarterly.csv
  multi_index_monthly.csv

The first run bootstraps:
  * quarter-end snapshots from Mar-2012 onward (for forward-return backtests)
  * month-end snapshots from Apr-2021 onward (for current consolidated-era PE percentile)
Subsequent runs append only newly completed periods.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import re
import time
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import requests

NSE_ARCHIVE_BASE = "https://nsearchives.nseindia.com/content/indices"
NSE_SOURCE_URL = "https://www.nseindia.com/all-reports"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36"
MODEL_VERSION = "multi-index-1.0"
REGIME_SEAM = pd.Timestamp("2021-03-31")
MIN_EXPANDING_OBS = 8
MIN_LIVE_MONTHS = 8
MIN_BACKTEST_QUARTERS = 12
BOOTSTRAP_QUARTER_START = date(2012, 3, 31)
BOOTSTRAP_MONTH_START = date(2021, 4, 30)

# Official broad-market and sectoral families from NSE pages. Normalised names are
# used only for presentation grouping; any other PE-bearing equity index is kept
# under Strategy / Thematic / Other.
BROAD_NAMES = {
    "NIFTY50","NIFTYNEXT50","NIFTY100","NIFTYNEXT100","NIFTY200",
    "NIFTYTOTALMARKET","NIFTY500","NIFTY500MULTICAP502525",
    "NIFTY500LARGEMIDSMALLEQUALCAPWEIGHTED","NIFTYMIDCAP150","NIFTYMIDCAP50",
    "NIFTYMIDCAPSELECT","NIFTYMIDCAP100","NIFTYSMALLCAP500","NIFTYSMALLCAP250",
    "NIFTYSMALLCAP50","NIFTYSMALLCAP100","NIFTYMICROCAP250","NIFTYLARGEMIDCAP250",
    "NIFTYMIDSMALLCAP400","NIFTYMIDSMALLCAP4005050","NIFTYINDIAFPI150"
}
SECTOR_NAMES = {
    "NIFTYAUTO","NIFTYBANK","NIFTYCEMENT","NIFTYCAPITALGOODS","NIFTYCHEMICALS",
    "NIFTYCOMMERCIALTRANSPORTSERVICES","NIFTYCONSTRUCTION","NIFTYCONSUMERSERVICES",
    "NIFTYFINANCIALSERVICES","NIFTYFINANCIALSERVICES2550","NIFTYFINANCIALSERVICESEXBANK",
    "NIFTYFMCG","NIFTYHEALTHCARE","NIFTYHEALTHCAREINDEX","NIFTYHOSPITALS",
    "NIFTYHOUSINGFINANCE","NIFTYINSURANCE","NIFTYIT","NIFTYMEDIA","NIFTYMETAL",
    "NIFTYNBFC","NIFTYPHARMA","NIFTYPOWER","NIFTYPRIVATEBANK","NIFTYPSUBANK",
    "NIFTYREALTY","NIFTYREITSREALTY","NIFTYRETAIL","NIFTYTELECOMMUNICATIONS",
    "NIFTYCONSUMERDURABLES","NIFTYOILGAS","NIFTY500HEALTHCARE",
    "NIFTYMIDSMALLFINANCIALSERVICES","NIFTYMIDSMALLHEALTHCARE","NIFTYMIDSMALLITTELECOM"
}

# Common archive abbreviations -> polished display label.
DISPLAY_MAP = {
    "NIFTY50":"NIFTY 50",
    "NIFTYNEXT50":"NIFTY Next 50",
    "NIFTY100":"NIFTY 100",
    "NIFTY200":"NIFTY 200",
    "NIFTY500":"NIFTY 500",
    "NIFTYTOTALMKT":"NIFTY Total Market",
    "NIFTYTOTALMARKET":"NIFTY Total Market",
    "NIFTYMIDCAP150":"NIFTY Midcap 150",
    "NIFTYMIDCAP50":"NIFTY Midcap 50",
    "NIFTYMIDCAP100":"NIFTY Midcap 100",
    "NIFTYMIDSELECT":"NIFTY Midcap Select",
    "NIFTYMIDCAPSELECT":"NIFTY Midcap Select",
    "NIFTYSMLCAP250":"NIFTY Smallcap 250",
    "NIFTYSMALLCAP250":"NIFTY Smallcap 250",
    "NIFTYSMLCAP100":"NIFTY Smallcap 100",
    "NIFTYSMALLCAP100":"NIFTY Smallcap 100",
    "NIFTYSMLCAP50":"NIFTY Smallcap 50",
    "NIFTYSMALLCAP50":"NIFTY Smallcap 50",
    "NIFTYMICROCAP250":"NIFTY Microcap 250",
    "NIFTYLARGEMID250":"NIFTY LargeMidcap 250",
    "NIFTYLARGEMIDCAP250":"NIFTY LargeMidcap 250",
    "NIFTYBANK":"NIFTY Bank",
    "NIFTYFINSERVICE":"NIFTY Financial Services",
    "NIFTYFINANCIALSERVICES":"NIFTY Financial Services",
    "NIFTYIT":"NIFTY IT",
    "NIFTYAUTO":"NIFTY Auto",
    "NIFTYFMCG":"NIFTY FMCG",
    "NIFTYPHARMA":"NIFTY Pharma",
    "NIFTYHEALTHCAREINDEX":"NIFTY Healthcare",
    "NIFTYHEALTHCARE":"NIFTY Healthcare",
    "NIFTYMETAL":"NIFTY Metal",
    "NIFTYREALTY":"NIFTY Realty",
    "NIFTYENERGY":"NIFTY Energy",
    "NIFTYOILANDGAS":"NIFTY Oil & Gas",
    "NIFTYOILGAS":"NIFTY Oil & Gas",
    "NIFTYMEDIA":"NIFTY Media",
    "NIFTYPSUBANK":"NIFTY PSU Bank",
    "NIFTYPVTBANK":"NIFTY Private Bank",
    "NIFTYPRIVATEBANK":"NIFTY Private Bank",
    "NIFTYCONSRDURBL":"NIFTY Consumer Durables",
    "NIFTYCONSUMERDURABLES":"NIFTY Consumer Durables",
    "NIFTYFINANCIALSERVICES2550":"NIFTY Financial Services 25/50",
}

# Equity-like exclusions where PE-based valuation is not economically meaningful.
EXCLUDE_TERMS = (
    "GSEC", "G-SEC", " GS ", "BOND", "SDL", "DEBT", "LIQUID", "MONEY MARKET",
    "ARBITRAGE", "FUTURES", "DIVIDEND POINT", "INVERSE", "LEVERAGE",
    "EQUITY SAVINGS", "DYNAMIC P/E", "DYNAMIC PE", "DYNAMIC P/B", "DYNAMIC PB"
)


def norm_name(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())




def canonical_key(raw: str) -> str:
    n = norm_name(raw)
    return norm_name(DISPLAY_MAP[n]) if n in DISPLAY_MAP else n

def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")
    return s or "index"


def prettify(raw: str) -> str:
    n = norm_name(raw)
    if n in DISPLAY_MAP:
        return DISPLAY_MAP[n]
    # Conservative title casing with NIFTY kept uppercase.
    x = re.sub(r"\s+", " ", str(raw).strip())
    words = []
    for w in x.split(" "):
        u = w.upper()
        if u in {"NIFTY","IT","FMCG","PSU","NBFC","REIT","REITS","ESG","FPI"}:
            words.append(u)
        elif u in {"50","100","150","200","250","400","500"}:
            words.append(u)
        else:
            words.append(w.title())
    return " ".join(words)


def classify_group(name: str) -> str:
    n = norm_name(name)
    # Handle common archive abbreviations before official-list comparison.
    alias = {
        "NIFTYTOTALMKT":"NIFTYTOTALMARKET",
        "NIFTYMIDSELECT":"NIFTYMIDCAPSELECT",
        "NIFTYSMLCAP250":"NIFTYSMALLCAP250",
        "NIFTYSMLCAP100":"NIFTYSMALLCAP100",
        "NIFTYSMLCAP50":"NIFTYSMALLCAP50",
        "NIFTYLARGEMID250":"NIFTYLARGEMIDCAP250",
        "NIFTYFINSERVICE":"NIFTYFINANCIALSERVICES",
        "NIFTYPVTBANK":"NIFTYPRIVATEBANK",
        "NIFTYCONSRDURBL":"NIFTYCONSUMERDURABLES",
        "NIFTYOILANDGAS":"NIFTYOILGAS",
    }.get(n, n)
    if alias in BROAD_NAMES:
        return "Broad Market"
    if alias in SECTOR_NAMES or n == "NIFTYENERGY":
        return "Sectoral"
    return "Strategy / Thematic / Other"


def is_equity_like(name: str) -> bool:
    u = f" {str(name).upper()} "
    if not u.strip().startswith("NIFTY"):
        return False
    return not any(term in u for term in EXCLUDE_TERMS)


def _norm_col(c: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(c).strip().lower())


def _find_col(df: pd.DataFrame, candidates: Iterable[str]) -> Optional[str]:
    normalized = {_norm_col(c): c for c in df.columns}
    for cand in candidates:
        if cand in normalized:
            return normalized[cand]
    for norm, orig in normalized.items():
        if any(cand in norm for cand in candidates):
            return orig
    return None


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/csv,text/plain,*/*",
        "Referer": "https://www.nseindia.com/",
    })
    return s


def fetch_snapshot_exact(s: requests.Session, d: date) -> Tuple[date, pd.DataFrame, str]:
    url = f"{NSE_ARCHIVE_BASE}/ind_close_all_{d:%d%m%Y}.csv"
    r = s.get(url, timeout=30)
    if r.status_code == 404:
        raise FileNotFoundError(url)
    r.raise_for_status()
    body = r.text.lstrip("\ufeff \t\r\n")
    if not body or body.startswith("<"):
        raise ValueError(f"Non-CSV response for {d}")
    df = pd.read_csv(io.StringIO(r.text))

    name_col = _find_col(df, ["indexname", "index"])
    close_col = _find_col(df, ["closingindexvalue", "closing", "close", "closingvalue"])
    pe_col = _find_col(df, ["pe", "peratio", "priceearnings", "priceearningsratio"])
    date_col = _find_col(df, ["indexdate", "date"])
    if name_col is None or close_col is None:
        raise ValueError(f"Could not identify index/close columns: {list(df.columns)}")

    out = pd.DataFrame({
        "IndexNameRaw": df[name_col].astype(str).str.strip(),
        "Close": pd.to_numeric(df[close_col], errors="coerce"),
        "PE": pd.to_numeric(df[pe_col], errors="coerce") if pe_col else float("nan"),
    })
    out = out[out["Close"].notna() & (out["Close"] > 0)].copy()
    out = out[out["IndexNameRaw"].map(is_equity_like)].copy()
    out["IndexKey"] = out["IndexNameRaw"].map(canonical_key)
    out["IndexName"] = out["IndexNameRaw"].map(prettify)
    out["Group"] = out["IndexNameRaw"].map(classify_group)

    out_date = d
    if date_col is not None:
        vals = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True).dropna()
        if len(vals):
            out_date = vals.iloc[0].date()
    return out_date, out, url


def fetch_snapshot_on_or_before(s: requests.Session, target: date, max_lookback: int = 12) -> Tuple[date, pd.DataFrame, str]:
    errors = []
    for i in range(max_lookback + 1):
        d = target - timedelta(days=i)
        try:
            return fetch_snapshot_exact(s, d)
        except FileNotFoundError:
            continue
        except Exception as e:
            errors.append(f"{d}: {e}")
            continue
    tail = "; ".join(errors[-3:]) if errors else "no trading-day archive found"
    raise RuntimeError(f"Unable to fetch NSE multi-index snapshot through {target}: {tail}")


def quarter_ends(start: date, end: date) -> List[date]:
    out = []
    y, m = start.year, start.month
    # snap to quarter-end month
    qm = ((m - 1) // 3 + 1) * 3
    cur = date(y, qm, monthrange(y, qm)[1])
    if cur < start:
        qm += 3
        if qm > 12:
            qm -= 12; y += 1
        cur = date(y, qm, monthrange(y, qm)[1])
    while cur <= end:
        out.append(cur)
        qm = cur.month + 3
        y = cur.year
        if qm > 12:
            qm -= 12; y += 1
        cur = date(y, qm, monthrange(y, qm)[1])
    return out


def month_ends(start: date, end: date) -> List[date]:
    out = []
    y, m = start.year, start.month
    cur = date(y, m, monthrange(y, m)[1])
    if cur < start:
        m += 1
        if m > 12:
            m = 1; y += 1
        cur = date(y, m, monthrange(y, m)[1])
    while cur <= end:
        out.append(cur)
        m = cur.month + 1
        y = cur.year
        if m > 12:
            m = 1; y += 1
        cur = date(y, m, monthrange(y, m)[1])
    return out


def latest_completed_quarter_end(d: date) -> date:
    qm = ((d.month - 1)//3 + 1)*3
    qe = date(d.year, qm, monthrange(d.year, qm)[1])
    if d >= qe:
        return qe
    qm -= 3
    y = d.year
    if qm <= 0:
        qm += 12; y -= 1
    return date(y, qm, monthrange(y, qm)[1])


def latest_completed_month_end(d: date) -> date:
    me = date(d.year, d.month, monthrange(d.year, d.month)[1])
    if d >= me:
        return me
    m = d.month - 1; y = d.year
    if m == 0:
        m = 12; y -= 1
    return date(y, m, monthrange(y, m)[1])


def append_period_snapshots(
    s: requests.Session,
    path: Path,
    periods: List[date],
    period_kind: str,
    sleep_seconds: float = 0.10,
) -> pd.DataFrame:
    cols = ["IndexKey","IndexName","Group","Date","SourceDate","Close","PE"]
    if path.exists():
        df = pd.read_csv(path)
    else:
        df = pd.DataFrame(columns=cols)

    have = set()
    if len(df):
        dates = pd.to_datetime(df["Date"], errors="coerce")
        have = {x.date() for x in dates.dropna().unique()}

    missing = [p for p in periods if p not in have]
    if not missing:
        return df

    additions = []
    for i, period_end in enumerate(missing, 1):
        try:
            source_date, snap, _ = fetch_snapshot_on_or_before(s, period_end, max_lookback=12)
        except Exception as e:
            print(f"WARNING: {period_kind} bootstrap skipped {period_end:%d-%b-%Y}: {e}")
            continue
        for _, r in snap.iterrows():
            additions.append({
                "IndexKey": r["IndexKey"],
                "IndexName": r["IndexName"],
                "Group": r["Group"],
                "Date": period_end.isoformat(),
                "SourceDate": source_date.isoformat(),
                "Close": float(r["Close"]),
                "PE": None if pd.isna(r["PE"]) else float(r["PE"]),
            })
        print(f"{period_kind}: added {period_end:%d-%b-%Y} from NSE {source_date:%d-%b-%Y} ({len(snap)} indices)")
        if sleep_seconds:
            time.sleep(sleep_seconds)

    out = pd.concat([df, pd.DataFrame(additions)], ignore_index=True, sort=False)
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
    out["SourceDate"] = pd.to_datetime(out["SourceDate"], errors="coerce")
    out["Close"] = pd.to_numeric(out["Close"], errors="coerce")
    out["PE"] = pd.to_numeric(out["PE"], errors="coerce")
    out = out.dropna(subset=["IndexKey","Date","Close"])
    out = out.sort_values(["IndexKey","Date","SourceDate"]).drop_duplicates(["IndexKey","Date"], keep="last")
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False, date_format="%Y-%m-%d", float_format="%.12g")
    return out


def percentile_midrank(values: Iterable[float], x: float) -> float:
    vals = [float(v) for v in values if pd.notna(v)]
    if not vals:
        return float("nan")
    lt = sum(v < x for v in vals)
    eq = sum(abs(v - x) < 1e-10 for v in vals)
    return (lt + 0.5*eq) / len(vals)


def rank_pct(values: Iterable[float], x: float) -> float:
    vals = [float(v) for v in values if pd.notna(v)]
    if not vals:
        return float("nan")
    lt = sum(v < x for v in vals)
    eq = sum(abs(v-x) < 1e-10 for v in vals)
    return (lt + (eq + 1)/2.0) / len(vals)


def growth_score(g: float) -> float:
    if pd.isna(g): return float("nan")
    if g <= 0: return 20.0
    if g < 0.05: return 20 + 400*g
    if g < 0.10: return 40 + 400*(g-0.05)
    if g < 0.15: return 60 + 400*(g-0.10)
    if g < 0.25: return 80 + 200*(g-0.15)
    return 100.0


def valuation_quintile(p: float) -> str:
    if pd.isna(p): return "Insufficient history"
    if p <= .20: return "Q1 Cheapest"
    if p <= .40: return "Q2"
    if p <= .60: return "Q3"
    if p <= .80: return "Q4"
    return "Q5 Most Expensive"


def linear_quantile(values: Iterable[float], q: float) -> float:
    vals = sorted(float(v) for v in values if pd.notna(v))
    if not vals:
        return float("nan")
    q = max(0.0, min(1.0, float(q)))
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals)-1)*q
    lo = int(math.floor(pos)); hi = int(math.ceil(pos))
    if lo == hi: return vals[lo]
    w = pos-lo
    return vals[lo]*(1-w)+vals[hi]*w


def signal_boundaries(pe_values: List[float], growth_score_value: float, current_eps: float, current_close: float):
    if len(pe_values) < MIN_LIVE_MONTHS or pd.isna(growth_score_value):
        return None
    wv, wg = .70, .30
    def one(threshold):
        req_val = (threshold - wg*growth_score_value)/wv
        req_val = max(0.0, min(100.0, req_val))
        target_pct = 1 - req_val/100.0
        pe = linear_quantile(pe_values, target_pct)
        nifty = current_eps*pe
        move = nifty/current_close - 1 if current_close else float("nan")
        return {
            "score_threshold": threshold,
            "valuation_score_required": req_val,
            "pe_percentile": target_pct,
            "pe": pe,
            "nifty_level": nifty,
            "move_from_current": move,
        }
    return {"buy_hold": one(70), "hold_sell": one(40)}


def build_quarterly_backtest(qdf: pd.DataFrame, key: str) -> Tuple[pd.DataFrame, Dict]:
    d = qdf[qdf["IndexKey"] == key].copy().sort_values("Date")
    if d.empty:
        return d, {"rows": [], "meta": {"status":"no_history"}}
    d["Date"] = pd.to_datetime(d["Date"])
    d["PE"] = pd.to_numeric(d["PE"], errors="coerce")
    d["Close"] = pd.to_numeric(d["Close"], errors="coerce")
    d["Regime"] = d["Date"].apply(lambda x: "Consolidated" if x >= REGIME_SEAM else "Standalone")
    d["EPS"] = d["Close"] / d["PE"]

    # YoY implied EPS growth (quarter aligned, same PE regime)
    eps_map = dict(zip(d["Date"], d["EPS"]))
    reg_map = dict(zip(d["Date"], d["Regime"]))
    growth = []
    for _, r in d.iterrows():
        prior = r["Date"] - pd.DateOffset(years=1)
        if prior in eps_map and reg_map.get(prior) == r["Regime"] and pd.notna(eps_map[prior]) and pd.notna(r["EPS"]):
            growth.append(float(r["EPS"] / eps_map[prior] - 1))
        else:
            growth.append(float("nan"))
    d["EPS_Growth_YoY"] = growth

    # Descriptive regime percentile/quintile only on valid PE observations.
    reg_pct = []
    for _, r in d.iterrows():
        if pd.isna(r["PE"]):
            reg_pct.append(float("nan")); continue
        vals = d.loc[(d["Regime"] == r["Regime"]) & d["PE"].notna(), "PE"].tolist()
        reg_pct.append(rank_pct(vals, float(r["PE"])))
    d["PE_Pct_Regime"] = reg_pct
    d["Valuation_Quintile"] = d["PE_Pct_Regime"].map(valuation_quintile)

    # Forward returns use price observations whether or not future PE is available.
    price_map = dict(zip(d["Date"], d["Close"]))
    for years, col in [(1,"Fwd_1Y"),(3,"Fwd_3Y"),(5,"Fwd_5Y"),(10,"Fwd_10Y")]:
        vals = []
        for _, r in d.iterrows():
            future = r["Date"] + pd.DateOffset(years=years)
            if future in price_map and pd.notna(price_map[future]) and pd.notna(r["Close"]):
                vals.append(float((price_map[future]/r["Close"])**(1/years)-1))
            else:
                vals.append(float("nan"))
        d[col] = vals

    rows = []
    order = ["Q1 Cheapest","Q2","Q3","Q4","Q5 Most Expensive"]
    for q in order:
        x = d[d["Valuation_Quintile"] == q]
        def med(col):
            s = pd.to_numeric(x[col], errors="coerce").dropna()
            return None if s.empty else float(s.median())
        s3 = pd.to_numeric(x["Fwd_3Y"], errors="coerce").dropna()
        rows.append({
            "quintile": q,
            "n": int(len(x)),
            "median_1y": med("Fwd_1Y"),
            "median_3y": med("Fwd_3Y"),
            "median_5y": med("Fwd_5Y"),
            "median_10y": med("Fwd_10Y"),
            "loss_3y": None if s3.empty else float((s3 < 0).mean()),
            "n_3y": int(len(s3)),
        })

    valid_pe = int(d["PE"].notna().sum())
    first = d["Date"].min().date().isoformat() if len(d) else None
    last = d["Date"].max().date().isoformat() if len(d) else None
    status = "ok" if valid_pe >= 12 else "insufficient_history"
    return d, {"rows": rows, "meta": {"status":status,"valid_pe_quarters":valid_pe,"first_quarter":first,"last_quarter":last}}


def find_prior_row(prior_snap: pd.DataFrame, key: str) -> Optional[pd.Series]:
    x = prior_snap[prior_snap["IndexKey"] == key]
    if x.empty: return None
    return x.iloc[0]


def build_current_for_index(
    key: str,
    latest_row: pd.Series,
    latest_date: date,
    prior_snap: pd.DataFrame,
    monthly: pd.DataFrame,
    quarter_bt: pd.DataFrame,
) -> Dict:
    close = float(latest_row["Close"])
    pe = None if pd.isna(latest_row["PE"]) else float(latest_row["PE"])
    prior = find_prior_row(prior_snap, key)
    prior_close = None if prior is None else float(prior["Close"])
    prior_pe = None if prior is None or pd.isna(prior["PE"]) else float(prior["PE"])

    monthly_idx = monthly[(monthly["IndexKey"] == key) & monthly["PE"].notna()].copy()
    monthly_idx["Date"] = pd.to_datetime(monthly_idx["Date"], errors="coerce")
    monthly_idx = monthly_idx[monthly_idx["Date"] >= REGIME_SEAM]
    pe_hist = [float(v) for v in monthly_idx["PE"].dropna().tolist()]

    eps = close/pe if pe and pe > 0 else None
    prior_eps = prior_close/prior_pe if prior_close and prior_pe and prior_pe > 0 else None
    growth = (eps/prior_eps - 1) if eps and prior_eps else None
    gs = growth_score(growth) if growth is not None else None

    if pe is not None and len(pe_hist) >= MIN_LIVE_MONTHS:
        pct_val = percentile_midrank(pe_hist + [pe], pe)
        valuation_score = 100*(1-pct_val)
    else:
        pct_val = None
        valuation_score = None

    if valuation_score is not None and gs is not None:
        composite = .70*valuation_score + .30*gs
        signal = "BUY" if composite >= 70 else ("HOLD" if composite >= 40 else "SELL")
    else:
        composite = None
        signal = None

    bounds = signal_boundaries(pe_hist + ([pe] if pe is not None else []), gs, eps, close) if eps and gs is not None else None
    med_pe = median(pe_hist) if pe_hist else None
    q = valuation_quintile(pct_val) if pct_val is not None else "Insufficient history"

    return {
        "index_key": key,
        "index_name": latest_row["IndexName"],
        "group": latest_row["Group"],
        "slug": slugify(latest_row["IndexName"]),
        "as_of": latest_date.isoformat(),
        "nifty_close": close,
        "pe": pe,
        "earnings_yield": (1/pe if pe else None),
        "implied_eps": eps,
        "yoy_eps_growth": growth,
        "pe_percentile": pct_val,
        "era_median_pe": med_pe,
        "valuation_quintile": q,
        "valuation_score": valuation_score,
        "growth_score": gs,
        "composite_score": composite,
        "signal": signal,
        "signal_boundaries": bounds,
        "live_pe_history_months": len(pe_hist),
        "backtest_valid_pe_quarters": int(quarter_bt["PE"].notna().sum()) if not quarter_bt.empty else 0,
        "model_version": MODEL_VERSION,
        "source": "NSE Indices daily archive",
    }


def append_daily_history(path: Path, latest_map: Dict[str, Dict]):
    cols = ["date","slug","index_name","group","close","pe","pe_percentile","yoy_eps_growth","valuation_score","growth_score","composite_score","signal"]
    if path.exists():
        hist = pd.read_csv(path)
        # Prune previously tracked indices that are no longer useful enough to
        # appear on the dashboard. Raw monthly/quarterly source history is kept
        # separately so an index can automatically re-enter later once it has
        # sufficient history.
        hist = hist[hist["slug"].isin(set(latest_map.keys()))].copy()
    else:
        hist = pd.DataFrame(columns=cols)
    rows = []
    for slug, x in latest_map.items():
        rows.append({
            "date": x["as_of"], "slug": slug, "index_name": x["index_name"], "group": x["group"],
            "close": x["nifty_close"], "pe": x["pe"], "pe_percentile": x["pe_percentile"],
            "yoy_eps_growth": x["yoy_eps_growth"], "valuation_score": x["valuation_score"],
            "growth_score": x["growth_score"], "composite_score": x["composite_score"], "signal": x["signal"],
        })
    out = pd.concat([hist, pd.DataFrame(rows)], ignore_index=True, sort=False)
    out = out.drop_duplicates(["date","slug"], keep="last").sort_values(["date","slug"])
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False, float_format="%.12g")


def make_catalog(latest_map: Dict[str, Dict], backtests: Dict[str, Dict]) -> Dict:
    items = []
    for slug, x in latest_map.items():
        bt_meta = backtests.get(slug, {}).get("meta", {})
        if x["pe"] is None:
            status = "pe_unavailable"
        elif x["composite_score"] is None:
            status = "insufficient_history"
        else:
            status = "ok"
        items.append({
            "slug": slug,
            "name": x["index_name"],
            "group": x["group"],
            "status": status,
            "valid_pe_quarters": bt_meta.get("valid_pe_quarters", 0),
            "live_pe_history_months": x.get("live_pe_history_months", 0),
        })
    group_order = {"Broad Market":0,"Sectoral":1,"Strategy / Thematic / Other":2}
    items.sort(key=lambda z:(group_order.get(z["group"],9), z["name"]))
    return {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "default_slug": next((i["slug"] for i in items if i["name"] == "NIFTY 50"), items[0]["slug"] if items else None),
        "groups": ["Broad Market","Sectoral","Strategy / Thematic / Other"],
        "items": items,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--web-dir", default="docs")
    ap.add_argument("--quarterly-csv", default="multi_index_quarterly.csv")
    ap.add_argument("--monthly-csv", default="multi_index_monthly.csv")
    ap.add_argument("--target-date", default=None, help="YYYY-MM-DD; defaults to today")
    args = ap.parse_args()

    web_dir = Path(args.web_dir)
    data_dir = web_dir / "data"
    qpath = Path(args.quarterly_csv)
    mpath = Path(args.monthly_csv)
    target = date.fromisoformat(args.target_date) if args.target_date else date.today()

    s = session()
    latest_date, latest_snap, latest_url = fetch_snapshot_on_or_before(s, target, max_lookback=12)
    # Approximately one year before the same observation date; last NSE trading day on/before.
    prior_target = date(latest_date.year - 1, latest_date.month, min(latest_date.day, monthrange(latest_date.year - 1, latest_date.month)[1]))
    prior_date, prior_snap, _ = fetch_snapshot_on_or_before(s, prior_target, max_lookback=12)

    q_end = latest_completed_quarter_end(latest_date)
    m_end = latest_completed_month_end(latest_date)
    q_periods = quarter_ends(BOOTSTRAP_QUARTER_START, q_end)
    m_periods = month_ends(BOOTSTRAP_MONTH_START, m_end)

    quarterly = append_period_snapshots(s, qpath, q_periods, "Quarterly")
    monthly = append_period_snapshots(s, mpath, m_periods, "Monthly")

    # Current universe is the equity-like rows present in latest NSE snapshot.
    # Preserve all such indices even if current PE is unavailable; website will flag them.
    current = latest_snap.copy().sort_values(["Group","IndexName"])

    latest_map: Dict[str, Dict] = {}
    backtests: Dict[str, Dict] = {}
    used_slugs = set()
    for _, row in current.iterrows():
        key = row["IndexKey"]
        qbt, summary = build_quarterly_backtest(quarterly, key)
        base_slug = slugify(row["IndexName"])
        slug = base_slug
        n = 2
        while slug in used_slugs:
            slug = f"{base_slug}-{n}"; n += 1
        used_slugs.add(slug)

        x = build_current_for_index(key, row, latest_date, prior_snap, monthly, qbt)
        x["slug"] = slug
        latest_map[slug] = x
        backtests[slug] = summary

    # Dashboard eligibility: show only indices for which the model is actually useful.
    # This removes "limited history" and live P/E-unavailable entries from the
    # selector/heatmap rather than displaying a weak or incomplete signal.
    #
    # Requirements:
    #   * current P/E is available
    #   * live composite score can be calculated
    #   * at least MIN_LIVE_MONTHS of comparable post-2021 P/E history
    #   * at least MIN_BACKTEST_QUARTERS PE-bearing quarter-end observations
    #
    # The full raw monthly/quarterly archive is still retained, so a currently
    # excluded index can automatically enter the dashboard once it matures.
    keep = set()
    excluded = []
    for slug, x in latest_map.items():
        bt_meta = backtests.get(slug, {}).get("meta", {})
        q_obs = int(bt_meta.get("valid_pe_quarters", 0) or 0)
        live_months = int(x.get("live_pe_history_months", 0) or 0)
        useful = (
            x.get("pe") is not None
            and x.get("composite_score") is not None
            and live_months >= MIN_LIVE_MONTHS
            and q_obs >= MIN_BACKTEST_QUARTERS
        )
        if useful:
            keep.add(slug)
        else:
            excluded.append((x.get("index_name", slug), live_months, q_obs, x.get("pe") is not None))

    latest_map = {k:v for k,v in latest_map.items() if k in keep}
    backtests = {k:v for k,v in backtests.items() if k in keep}

    if excluded:
        print(f"Excluded {len(excluded)} limited/unusable indices from dashboard.")
        for name, live_months, q_obs, has_pe in excluded:
            print(f"  - {name}: live_months={live_months}, quarter_pe_obs={q_obs}, current_pe={'yes' if has_pe else 'no'}")

    catalog = make_catalog(latest_map, backtests)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "multi_index_catalog.json").write_text(json.dumps(catalog, indent=2, allow_nan=False), encoding="utf-8")
    (data_dir / "multi_index_latest.json").write_text(json.dumps({
        "as_of": latest_date.isoformat(),
        "prior_as_of": prior_date.isoformat(),
        "source_url": latest_url,
        "indices": latest_map,
    }, indent=2, allow_nan=False), encoding="utf-8")
    (data_dir / "multi_index_backtests.json").write_text(json.dumps({
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "indices": backtests,
    }, indent=2, allow_nan=False), encoding="utf-8")
    append_daily_history(data_dir / "multi_index_history.csv", latest_map)

    ok = sum(1 for x in latest_map.values() if x.get("signal"))
    print(f"Multi-index tracker updated: {len(latest_map)} eligible indices; {ok} with live signal; as of {latest_date}")


if __name__ == "__main__":
    main()
