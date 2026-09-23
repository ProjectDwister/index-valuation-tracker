#!/usr/bin/env python3
"""Refresh official NSE/Nifty index constituents and published weights.

Data source
-----------
NSE Indices monthly report: "Indices Market Capitalisation & Weightage".
The public report is exposed as a ZIP whose filename follows the pattern
``indices_data{Mon}{YYYY}.zip``. The archive can contain PDF/CSV/XLS/XLSX
files. This script is deliberately format-tolerant and parses whichever
representation NSE publishes for that month. For indices absent from the
monthly report, the official index pages link to constituent CSVs. Those CSVs
are used for membership only when they do not publish weights.

Outputs
-------
- docs/data/composition/<slug>.json
- docs/data/composition/manifest.json
- docs/data/composition/status.json
- adds/replaces a "Composition" worksheet in docs/downloads/indices/*.xlsx

The script never fabricates weights. Existing *official* files are retained
if a transient download/parsing failure occurs; old demo files are removed.
"""
from __future__ import annotations

import argparse
import calendar
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import json
import os
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

try:
    import pdfplumber
except Exception:
    pdfplumber = None

try:
    import cloudscraper
except Exception:
    cloudscraper = None

REPORT_PAGE = "https://www.niftyindices.com/reports/monthly-reports"
REPORT_BASES = [
    "https://www.niftyindices.com/Indices_-_Market_Capitalisation_and_Weightage",
    "https://niftyindices.com/Indices_-_Market_Capitalisation_and_Weightage",
]
INDEX_CATEGORIES = (
    "broad-based-indices", "sectoral-indices", "thematic-indices", "strategy-indices"
)
INDEX_BASE = "https://www.niftyindices.com/indices/equity/"
CONSTITUENT_BASE = "https://www.niftyindices.com"
# The Auto index page displays an empty download link; this CSV is still
# published on the official host. Validate its content just like linked files.
CONSTITUENT_URL_FALLBACKS = {
    "nifty-auto": "https://www.niftyindices.com/IndexConstituent/ind_niftyautolist.csv",
}
MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

COMPANY_KEYS = ("company", "security", "constituent", "stock", "issuer", "name of security")
SYMBOL_KEYS = ("symbol", "ticker")
SECTOR_KEYS = ("sector", "industry", "basic industry")
WEIGHT_KEYS = ("weight", "weightage")
INDEX_KEYS = ("index name", "index", "benchmark")


def norm_space(v) -> str:
    return re.sub(r"\s+", " ", str(v or "").replace("\n", " ").strip())


def compact(v) -> str:
    return re.sub(r"[^A-Z0-9]", "", norm_space(v).upper())


def clean_header(v) -> str:
    return norm_space(v).lower().replace("%", " percent ")


def clean_company(v) -> str:
    s = norm_space(v)
    s = re.sub(r"^\s*\d+[\.)-]?\s+", "", s)
    return s.strip(" -|:")


def parse_weight(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)) and pd.notna(v):
        return float(v)
    s = norm_space(v).replace(",", "")
    if not s or s.lower() in {"nan", "na", "n/a", "-", "—"}:
        return None
    s = s.replace("%", "")
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def safe_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def atomic_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def catalog_index_map(catalog: dict) -> Dict[str, dict]:
    return {i["slug"]: i for i in catalog.get("items", [])}


def title_matcher(catalog: dict):
    items = []
    for item in catalog.get("items", []):
        c = compact(item.get("name"))
        if c:
            items.append((len(c), c, item["slug"], item.get("name")))
    items.sort(reverse=True)

    aliases = {
        "NIFTYMIDSMALLCAP400": "nifty-midsmallcap-400",
        "NIFTY100ESG": "nifty100-esg",
        "NIFTY100ENHANCEDESG": "nifty100-enhanced-esg",
        "NIFTY100EQUALWEIGHT": "nifty100-equal-weight",
        "NIFTY100LOWVOLATILITY30": "nifty100-low-volatility-30",
        "NIFTY100QUALITY30": "nifty100-quality-30",
        "NIFTY200QUALITY30": "nifty200-quality-30",
        "NIFTY50EQUALWEIGHT": "nifty50-equal-weight",
        "NIFTY50SHARIAH": "nifty50-shariah",
        "NIFTY50VALUE20": "nifty50-value-20",
        "NIFTY500SHARIAH": "nifty500-shariah",
        "NIFTYSMEEMERGE": "nifty-sme-emerge",
    }

    def match(text: str) -> Optional[str]:
        c = compact(text)
        if not c:
            return None
        for a, slug in aliases.items():
            if a in c:
                return slug
        for _, key, slug, _ in items:
            if key in c:
                return slug
        return None

    return match


def browser_headers() -> dict:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
        "Accept": "application/zip,application/octet-stream,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": REPORT_PAGE,
        "Connection": "keep-alive",
    }


class LinkParser(HTMLParser):
    """Collect anchor labels and destinations without depending on page layout."""

    def __init__(self):
        super().__init__()
        self.links = []
        self.href = None
        self.label = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.label = []

    def handle_data(self, data):
        if self.href is not None:
            self.label.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            self.links.append((norm_space(" ".join(self.label)), self.href))
            self.href = None


def official_url(url: str, path_prefix: str) -> Optional[str]:
    """Only follow HTTPS links on the actual NSE Indices host."""
    absolute = urljoin(CONSTITUENT_BASE, url)
    parsed = urlparse(absolute)
    if (parsed.scheme == "https" and parsed.hostname in {"niftyindices.com", "www.niftyindices.com"}
            and parsed.path.lower().startswith(path_prefix.lower())):
        return absolute
    return None


def official_get(url: str) -> bytes:
    headers = browser_headers()
    headers["Accept"] = "text/html,text/csv,application/octet-stream,*/*"
    r = requests.get(url, headers=headers, timeout=25)
    r.raise_for_status()
    return r.content


def discover_index_pages(catalog: dict) -> Dict[str, str]:
    """Match index names exactly against links on NSE Indices category pages."""
    names = {compact(item["name"]): item["slug"] for item in catalog.get("items", [])}
    pages = {}
    for category in INDEX_CATEGORIES:
        try:
            root = f"{INDEX_BASE}{category}"
            parser = LinkParser()
            parser.feed(official_get(root).decode("utf-8", errors="replace"))
            for label, href in parser.links:
                slug = names.get(compact(label))
                url = official_url(href, f"/indices/equity/{category}/")
                if slug and url:
                    pages[slug] = url
        except Exception as e:
            print(f"WARNING: official index page discovery failed for {category}: {e}")
    return pages


def parse_constituent_csv(content: bytes, item: dict, url: str, fetched_on: str) -> dict:
    """Read a verified official stock list; a membership file may omit weights."""
    if content.lstrip().lower().startswith((b"<!doctype", b"<html")):
        raise ValueError("Constituent download returned HTML")
    frame = None
    for encoding in ("utf-8-sig", "cp1252", "latin1"):
        try:
            raw = pd.read_csv(io.BytesIO(content), encoding=encoding, dtype=str, header=None, on_bad_lines="skip")
            for n in range(min(8, len(raw))):
                names = [clean_header(c) for c in raw.iloc[n].fillna("")]
                if any("company" in c or "security" in c for c in names) and any("symbol" in c for c in names):
                    frame = raw.iloc[n + 1 :].copy()
                    frame.columns = raw.iloc[n].fillna("").tolist()
                    break
            if frame is not None:
                break
        except (UnicodeError, pd.errors.ParserError, ValueError):
            continue
    if frame is None:
        raise ValueError("Constituent CSV has no company/symbol columns")
    cols = list(frame.columns)
    company = pick_col(cols, COMPANY_KEYS)
    symbol = pick_col(cols, SYMBOL_KEYS)
    sector = pick_col(cols, SECTOR_KEYS)
    weight = pick_col(cols, WEIGHT_KEYS)
    holdings = []
    seen = set()
    for _, row in frame.iterrows():
        ticker = norm_space(row.get(symbol))
        name = clean_company(row.get(company))
        if not ticker or not name or ticker.lower() in {"nan", "symbol"}:
            continue
        if ticker.upper() in seen:
            continue
        seen.add(ticker.upper())
        w = parse_weight(row.get(weight)) if weight else None
        holdings.append({
            "name": name, "symbol": ticker,
            "sector": norm_space(row.get(sector)) if sector and pd.notna(row.get(sector)) else "",
            "weight": w if w is not None and 0 < w <= 100 else None,
        })
    if len(holdings) < 3:
        raise ValueError(f"Only {len(holdings)} valid constituents found")
    weighted = [h for h in holdings if h["weight"] is not None]
    coverage = sum(h["weight"] for h in weighted)
    if len(weighted) == len(holdings) and 0.97 <= coverage <= 1.03:
        for h in holdings:
            h["weight"] = round(h["weight"] * 100, 6)
        coverage *= 100
    complete_weights = len(weighted) == len(holdings) and 97 <= coverage <= 103
    if not complete_weights:
        # Partial/ambiguous columns cannot support index weight claims.
        for h in holdings:
            h["weight"] = None
    if complete_weights:
        holdings.sort(key=lambda h: h["weight"], reverse=True)
    else:
        holdings.sort(key=lambda h: h["name"].casefold())
    sectors = sector_rows(holdings) if complete_weights else sector_count_rows(holdings)
    return {
        "slug": item["slug"], "index_name": item["name"],
        "as_of": None, "retrieved_on": fetched_on,
        "source": "NSE Indices — Index Constituent CSV", "source_url": url,
        "source_type": "official_nse_indices_constituent_csv",
        "completeness": "full" if complete_weights else "constituents",
        "stock_count": len(holdings),
        "weight_coverage": round(coverage, 4) if complete_weights else None,
        "top10_weight": round(sum(h["weight"] for h in holdings[:10]), 4) if complete_weights else None,
        "coverage_note": (
            "The official constituent file publishes weights."
            if complete_weights else
            "The official constituent file lists members but does not publish usable stock weights."
        ),
        "holdings": holdings, "sectors": sectors,
    }


def sector_count_rows(holdings: List[dict]) -> List[dict]:
    buckets = {}
    for holding in holdings:
        name = holding.get("sector")
        if name:
            buckets[name] = buckets.get(name, 0) + 1
    total = len(holdings)
    return [
        {"name": name, "count": count, "stock_share": round(count / total * 100, 4), "weight": None}
        for name, count in sorted(buckets.items(), key=lambda row: (-row[1], row[0]))
    ]


def fetch_constituent_file(item: dict, page_url: str, fetched_on: str) -> dict:
    parser = LinkParser()
    parser.feed(official_get(page_url).decode("utf-8", errors="replace"))
    for label, href in parser.links:
        if compact(label) == "INDEXCONSTITUENT":
            url = official_url(href, "/IndexConstituent/")
            if url and urlparse(url).path.lower().endswith(".csv"):
                return parse_constituent_csv(official_get(url), item, url, fetched_on)
    fallback = CONSTITUENT_URL_FALLBACKS.get(item["slug"])
    if fallback:
        return parse_constituent_csv(official_get(fallback), item, fallback, fetched_on)
    raise ValueError("No official Index Constituent CSV link on index page")


def fetch_missing_constituents(catalog: dict, already_weighted: dict) -> Tuple[dict, dict]:
    pages = discover_index_pages(catalog)
    missing = [item for item in catalog.get("items", []) if item["slug"] not in already_weighted]
    results = {}
    errors = {}
    fetched_on = date.today().isoformat()
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {
            pool.submit(fetch_constituent_file, item, pages[item["slug"]], fetched_on): item["slug"]
            for item in missing if item["slug"] in pages
        }
        for future in as_completed(pending):
            slug = pending[future]
            try:
                results[slug] = future.result()
            except Exception as e:
                errors[slug] = str(e)
    for item in missing:
        if item["slug"] not in pages:
            errors[item["slug"]] = "Index page not found in official categories"
    return results, errors


def candidate_months(today: date, count: int = 8) -> Iterable[Tuple[int, int]]:
    y, m = today.year, today.month
    for _ in range(count):
        yield y, m
        m -= 1
        if m == 0:
            m = 12
            y -= 1


def is_zip_bytes(content: bytes) -> bool:
    try:
        return zipfile.is_zipfile(io.BytesIO(content))
    except Exception:
        return False


def fetch_report_zip(today: date, max_months: int = 8) -> Tuple[bytes, str, str]:
    sessions = []
    s = requests.Session()
    s.headers.update(browser_headers())
    sessions.append(("requests", s))
    if cloudscraper is not None:
        try:
            cs = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "mobile": False})
            cs.headers.update(browser_headers())
            sessions.append(("cloudscraper", cs))
        except Exception:
            pass

    errors = []
    for year, month in candidate_months(today, max_months):
        mon = MONTH_ABBR[month - 1]
        filename = f"indices_data{mon}{year}.zip"
        for base in REPORT_BASES:
            url = f"{base}/{filename}"
            for label, sess in sessions:
                try:
                    r = sess.get(url, timeout=35, allow_redirects=True)
                    if r.ok and is_zip_bytes(r.content):
                        return r.content, url, f"{mon} {year}"
                    errors.append(f"{label} {url}: HTTP {r.status_code}, {len(r.content)} bytes")
                except Exception as e:
                    errors.append(f"{label} {url}: {e}")
    raise RuntimeError("Unable to download an official monthly composition report. " + " | ".join(errors[-8:]))


def find_header_row(raw: pd.DataFrame, scan: int = 18) -> Optional[int]:
    for r in range(min(scan, len(raw))):
        vals = [clean_header(x) for x in raw.iloc[r].tolist()]
        if any(any(k in v for k in WEIGHT_KEYS) for v in vals):
            return r
    return None


def frame_with_header(raw: pd.DataFrame) -> Optional[pd.DataFrame]:
    if raw is None or raw.empty:
        return None
    raw = raw.dropna(axis=1, how="all").dropna(axis=0, how="all")
    if raw.empty:
        return None
    hr = find_header_row(raw)
    if hr is None:
        # If pandas already supplied meaningful columns, keep them.
        cols = [clean_header(x) for x in raw.columns]
        if any(any(k in c for k in WEIGHT_KEYS) for c in cols):
            df = raw.copy()
        else:
            return None
    else:
        cols = [norm_space(x) or f"col_{i}" for i, x in enumerate(raw.iloc[hr].tolist())]
        df = raw.iloc[hr + 1 :].copy()
        df.columns = cols
    df = df.dropna(axis=0, how="all").dropna(axis=1, how="all")
    return df if not df.empty else None


def pick_col(columns: List[str], keys: Tuple[str, ...]) -> Optional[str]:
    for c in columns:
        h = clean_header(c)
        if any(k in h for k in keys):
            return c
    return None


def dataframe_records(df: pd.DataFrame, default_slug: Optional[str], match_title, source_hint: str) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    f = frame_with_header(df)
    if f is None:
        return out
    cols = list(f.columns)
    wcol = pick_col(cols, WEIGHT_KEYS)
    ccol = pick_col(cols, COMPANY_KEYS)
    scol = pick_col(cols, SYMBOL_KEYS)
    seccol = pick_col(cols, SECTOR_KEYS)
    icol = pick_col(cols, INDEX_KEYS)
    if not wcol or not ccol:
        return out

    for _, row in f.iterrows():
        company = clean_company(row.get(ccol))
        if not company or company.lower().startswith("total"):
            continue
        weight = parse_weight(row.get(wcol))
        if weight is None or weight <= 0 or weight > 100:
            continue
        slug = default_slug
        if icol and pd.notna(row.get(icol)):
            slug = match_title(str(row.get(icol))) or slug
        if not slug:
            continue
        rec = {
            "name": company,
            "symbol": norm_space(row.get(scol)) if scol and pd.notna(row.get(scol)) else "",
            "sector": norm_space(row.get(seccol)) if seccol and pd.notna(row.get(seccol)) else "",
            "weight": round(float(weight), 6),
            "source_hint": source_hint,
        }
        out.setdefault(slug, []).append(rec)
    return out


def merge_records(target: Dict[str, List[dict]], incoming: Dict[str, List[dict]]):
    for slug, rows in incoming.items():
        target.setdefault(slug, []).extend(rows)


def parse_csv_bytes(data: bytes, filename: str, match_title) -> Dict[str, List[dict]]:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        try:
            text = data.decode(enc)
            raw = pd.read_csv(io.StringIO(text), header=None, dtype=object, engine="python")
            slug = match_title(filename + " " + " ".join(map(str, raw.head(8).fillna("").values.flatten())))
            return dataframe_records(raw, slug, match_title, filename)
        except Exception:
            continue
    return {}


def parse_excel_bytes(data: bytes, filename: str, match_title) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    for engine in (None, "openpyxl", "xlrd"):
        try:
            xls = pd.ExcelFile(io.BytesIO(data), engine=engine)
            for sheet in xls.sheet_names:
                raw = pd.read_excel(xls, sheet_name=sheet, header=None, dtype=object)
                intro = " ".join(map(str, raw.head(8).fillna("").values.flatten()))
                slug = match_title(f"{filename} {sheet} {intro}")
                merge_records(out, dataframe_records(raw, slug, match_title, f"{filename}:{sheet}"))
            if out:
                return out
        except Exception:
            continue
    return out


def pdf_date(text: str) -> Optional[str]:
    if not text:
        return None
    for pattern in (
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(20\d{2})\b",
        r"\b(\d{1,2})[-\s](Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[-\s](20\d{2})\b",
    ):
        m = re.search(pattern, text, flags=re.I)
        if m:
            try:
                if m.lastindex == 3 and m.group(1).isalpha() and len(m.group(1)) > 3:
                    dt = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y")
                else:
                    dt = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %b %Y")
                return dt.date().isoformat()
            except Exception:
                pass
    return None



def pdf_word_table_records(page, default_slug: Optional[str], filename: str) -> Dict[str, List[dict]]:
    """Extract rows from PDF text using header x-positions.

    This is a fallback for NSE PDFs that visually contain a table but do not
    expose ruled lines to pdfplumber's default table extractor.
    """
    out: Dict[str, List[dict]] = {}
    try:
        words = page.extract_words(use_text_flow=True, keep_blank_chars=False) or []
    except Exception:
        return out
    if not words:
        return out

    # Group words by visual line (top coordinate).
    lines: List[List[dict]] = []
    for w in sorted(words, key=lambda z: (round(float(z.get("top", 0)), 1), float(z.get("x0", 0)))):
        top = float(w.get("top", 0))
        target = None
        for line in reversed(lines[-4:]):
            if abs(float(line[0].get("top", 0)) - top) <= 2.2:
                target = line
                break
        if target is None:
            target = []
            lines.append(target)
        target.append(w)

    for i, line in enumerate(lines):
        joined = " ".join(w.get("text", "") for w in line)
        low = clean_header(joined)
        if not any(k in low for k in WEIGHT_KEYS):
            continue
        if not any(k in low for k in COMPANY_KEYS):
            continue

        # Infer column starts from header tokens.
        tokens = sorted(line, key=lambda z: float(z.get("x0", 0)))
        def token_x(keys):
            for w in tokens:
                t = clean_header(w.get("text", ""))
                if any(k in t for k in keys):
                    return float(w.get("x0", 0))
            return None

        company_x = token_x(("company", "security", "constituent", "stock", "issuer", "name"))
        sector_x = token_x(("industry", "sector"))
        symbol_x = token_x(("symbol", "ticker"))
        weight_x = token_x(("weight", "weightage"))
        if weight_x is None:
            continue
        if company_x is None:
            company_x = min(float(w.get("x0", 0)) for w in tokens)

        # Read subsequent visual lines while they look like data rows.
        misses = 0
        for row_line in lines[i + 1 :]:
            row_top = float(row_line[0].get("top", 0))
            # Stop at obvious new section headers after a couple of misses.
            row_text = " ".join(w.get("text", "") for w in row_line).strip()
            if not row_text:
                continue
            cols = {"company": [], "sector": [], "symbol": [], "weight": []}
            for w in sorted(row_line, key=lambda z: float(z.get("x0", 0))):
                x = float(w.get("x0", 0))
                txt = w.get("text", "")
                if x >= weight_x - 2:
                    cols["weight"].append(txt)
                elif symbol_x is not None and x >= symbol_x - 2:
                    cols["symbol"].append(txt)
                elif sector_x is not None and x >= sector_x - 2:
                    cols["sector"].append(txt)
                else:
                    cols["company"].append(txt)
            wt = parse_weight(" ".join(cols["weight"]))
            company = clean_company(" ".join(cols["company"]))
            if wt is None or not company or wt <= 0 or wt > 100:
                misses += 1
                if misses >= 3:
                    break
                continue
            misses = 0
            rec = {
                "name": company,
                "symbol": norm_space(" ".join(cols["symbol"])),
                "sector": norm_space(" ".join(cols["sector"])),
                "weight": round(float(wt), 6),
                "source_hint": filename,
            }
            if default_slug:
                out.setdefault(default_slug, []).append(rec)
        # One constituent table per page is normally sufficient.
        if out:
            break
    return out


def _group_pdf_words_into_lines(page, tolerance: float = 2.5) -> List[List[dict]]:
    try:
        words = page.extract_words(use_text_flow=True, keep_blank_chars=False) or []
    except Exception:
        return []
    lines: List[List[dict]] = []
    for w in sorted(words, key=lambda z: (float(z.get("top", 0)), float(z.get("x0", 0)))):
        top = float(w.get("top", 0))
        target = None
        for line in reversed(lines[-5:]):
            if abs(float(line[0].get("top", 0)) - top) <= tolerance:
                target = line
                break
        if target is None:
            target = []
            lines.append(target)
        target.append(w)
    return [sorted(line, key=lambda z: float(z.get("x0", 0))) for line in lines]


def _numeric_token_value(text: str) -> Optional[float]:
    s = norm_space(text).replace(",", "").replace("%", "")
    if not s:
        return None
    # Accept plain signed numbers only. This intentionally rejects symbols such
    # as 3M, dates, ISINs and alpha-numeric tickers.
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", s):
        return None
    try:
        return float(s)
    except Exception:
        return None


def pdf_loose_row_records(page, default_slug: Optional[str], filename: str) -> Dict[str, List[dict]]:
    """Last-resort PDF parser for NSE weightage reports.

    Some monthly PDFs are visually tabular but expose neither table borders nor
    a single-line header to pdfplumber. In those files each constituent row
    still has a stable visual line: optional rank, company name, one or more
    market-cap columns and a final weight/weightage percentage. This parser
    takes the final numeric token as weight and the leading text block as the
    company name. It deliberately leaves symbol/sector blank rather than
    guessing them.
    """
    if not default_slug:
        return {}
    out: Dict[str, List[dict]] = {}
    skip_phrases = (
        "market capitalisation", "market capitalization", "weightage", "weight (%)",
        "weight(%)", "company name", "company's name", "company’s name", "name of security",
        "free float", "full market cap", "rank", "constituent", "index name",
        "as on", "total", "source", "note", "nse indices", "page ",
    )
    for line in _group_pdf_words_into_lines(page):
        if len(line) < 2:
            continue
        tokens = [norm_space(w.get("text", "")) for w in line if norm_space(w.get("text", ""))]
        if len(tokens) < 2:
            continue
        joined = " ".join(tokens)
        low = joined.lower()
        if any(p in low for p in skip_phrases):
            continue

        numeric_positions = [(i, _numeric_token_value(tok)) for i, tok in enumerate(tokens)]
        numeric_positions = [(i, v) for i, v in numeric_positions if v is not None]
        if not numeric_positions:
            continue
        last_i, weight = numeric_positions[-1]
        if weight is None or weight <= 0 or weight > 100:
            continue

        # Exclude a leading serial/rank from the company-name slice.
        start = 0
        if numeric_positions and numeric_positions[0][0] == 0:
            v0 = numeric_positions[0][1]
            if v0 is not None and float(v0).is_integer() and 0 < v0 <= 2000:
                start = 1

        # Company name usually ends immediately before the first numeric market-
        # cap field. If the only number is the final weight, everything between
        # rank and weight is treated as the name.
        first_data_numeric = None
        for i, v in numeric_positions:
            if i >= start and i != last_i:
                first_data_numeric = i
                break
        end = first_data_numeric if first_data_numeric is not None else last_i
        if end <= start:
            continue
        company = clean_company(" ".join(tokens[start:end]))
        # pdfplumber can occasionally attach the first numeric market-cap token
        # to the preceding text run. Remove trailing standalone numeric fields
        # rather than showing them as part of the company name.
        company = re.sub(r"(?:\s+[-+]?\d[\d,]*(?:\.\d+)?)+\s*$", "", company).strip()
        if not company or len(company) < 3:
            continue
        # Avoid lines that are clearly headings/summary labels rather than stocks.
        if compact(company) in {"NIFTY50", "NIFTY100", "NIFTY200", "NIFTY500"}:
            continue
        if not re.search(r"[A-Za-z]", company):
            continue

        out.setdefault(default_slug, []).append({
            "name": company,
            "symbol": "",
            "sector": "",
            "weight": round(float(weight), 6),
            "source_hint": filename,
        })
    return out


def detect_page_slug(page_text: str, match_title, prior_slug: Optional[str]) -> Optional[str]:
    """Prefer a title match from the top of a page; otherwise carry prior index."""
    lines = [norm_space(x) for x in (page_text or "").splitlines() if norm_space(x)]
    # Report section headings are normally near the top of a page. Looking line
    # by line prevents a parent-index reference in descriptive text from
    # overriding the actual section heading.
    for line in lines[:18]:
        slug = match_title(line)
        if slug:
            return slug
    slug = match_title(" ".join(lines[:40]))
    return slug or prior_slug

def parse_pdf_bytes(data: bytes, filename: str, match_title) -> Tuple[Dict[str, List[dict]], Dict[str, str]]:
    out: Dict[str, List[dict]] = {}
    dates: Dict[str, str] = {}
    if pdfplumber is None:
        return out, dates
    try:
        current_slug: Optional[str] = None
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page_no, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                current_slug = detect_page_slug(text, match_title, current_slug)
                slug = current_slug
                if slug:
                    dt = pdf_date(text)
                    if dt:
                        dates[slug] = dt

                tables = []
                try:
                    tables = page.extract_tables() or []
                except Exception:
                    tables = []
                page_before = sum(len(v) for v in out.values())
                for tbl in tables:
                    if not tbl or len(tbl) < 2:
                        continue
                    raw = pd.DataFrame(tbl)
                    merge_records(out, dataframe_records(raw, slug, match_title, f"{filename}:p{page_no}"))

                page_after = sum(len(v) for v in out.values())
                if page_after == page_before and slug:
                    word_candidate = pdf_word_table_records(page, slug, f"{filename}:p{page_no}")
                    loose_candidate = pdf_loose_row_records(page, slug, f"{filename}:p{page_no}")

                    def candidate_score(candidate):
                        rows = candidate.get(slug, []) if candidate else []
                        unique = len({compact(clean_company(r.get("name"))) for r in rows if clean_company(r.get("name"))})
                        weight_sum = sum(float(r.get("weight") or 0) for r in rows)
                        # Unique names are the strongest signal; row count is next.
                        # A plausible cumulative weight breaks ties.
                        plausibility = 1 if 0 < weight_sum <= 105 else 0
                        return (unique, len(rows), plausibility)

                    chosen = loose_candidate if candidate_score(loose_candidate) > candidate_score(word_candidate) else word_candidate
                    merge_records(out, chosen)
    except Exception as e:
        print(f"WARNING: PDF parse failed for {filename}: {e}")
    return out, dates


def dedupe_and_validate(rows: List[dict]) -> Tuple[List[dict], float]:
    # Keep the last record for a duplicate symbol/name; repeated headers across PDF pages are common.
    dedup: Dict[str, dict] = {}
    for r in rows:
        name = clean_company(r.get("name"))
        if not name:
            continue
        symbol = norm_space(r.get("symbol"))
        key = compact(symbol) or compact(name)
        if not key:
            continue
        rec = {
            "name": name,
            "symbol": symbol,
            "sector": norm_space(r.get("sector")),
            "weight": round(float(r.get("weight") or 0), 6),
        }
        if rec["weight"] > 0:
            dedup[key] = rec
    cleaned = sorted(dedup.values(), key=lambda x: x["weight"], reverse=True)
    total = sum(r["weight"] for r in cleaned)
    # Some data sources encode 0-1 fractions. Convert if the whole portfolio is around 1.
    if cleaned and total <= 1.5:
        for r in cleaned:
            r["weight"] = round(r["weight"] * 100.0, 6)
        total = sum(r["weight"] for r in cleaned)
    return cleaned, total


def sector_rows(holdings: List[dict]) -> List[dict]:
    buckets: Dict[str, dict] = {}
    for h in holdings:
        sec = norm_space(h.get("sector"))
        if not sec:
            continue
        b = buckets.setdefault(sec, {"name": sec, "weight": 0.0, "count": 0})
        b["weight"] += float(h.get("weight") or 0)
        b["count"] += 1
    rows = list(buckets.values())
    for r in rows:
        r["weight"] = round(r["weight"], 6)
    return sorted(rows, key=lambda x: x["weight"], reverse=True)



def parse_archive_members(content: bytes, archive_name: str, match_title, depth: int = 0) -> Tuple[Dict[str, List[dict]], Dict[str, str], List[str], List[str]]:
    """Parse an NSE report ZIP recursively, including nested ZIP archives."""
    out: Dict[str, List[dict]] = {}
    dates: Dict[str, str] = {}
    files_seen: List[str] = []
    errors: List[str] = []
    if depth > 3:
        return out, dates, files_seen, [f"nested ZIP depth exceeded: {archive_name}"]
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except Exception as e:
        return out, dates, files_seen, [f"invalid ZIP {archive_name}: {e}"]
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            qualified = f"{archive_name}!{name}" if archive_name else name
            suffix = Path(name).suffix.lower()
            data = zf.read(info)
            files_seen.append(qualified)
            try:
                if suffix == ".zip" or is_zip_bytes(data):
                    nested, ndates, nfiles, nerrors = parse_archive_members(data, qualified, match_title, depth + 1)
                    merge_records(out, nested)
                    dates.update(ndates)
                    files_seen.extend(nfiles)
                    errors.extend(nerrors)
                elif suffix in {".csv", ".txt"}:
                    merge_records(out, parse_csv_bytes(data, qualified, match_title))
                elif suffix in {".xls", ".xlsx"}:
                    merge_records(out, parse_excel_bytes(data, qualified, match_title))
                elif suffix == ".pdf":
                    parsed, pdates = parse_pdf_bytes(data, qualified, match_title)
                    merge_records(out, parsed)
                    dates.update(pdates)
                elif suffix in {".html", ".htm"}:
                    try:
                        for idx, df in enumerate(pd.read_html(io.BytesIO(data))):
                            intro = " ".join(map(str, df.head(8).fillna("").values.flatten()))
                            slug = match_title(f"{qualified} {intro}")
                            merge_records(out, dataframe_records(df, slug, match_title, f"{qualified}:table{idx+1}"))
                    except Exception as e:
                        errors.append(f"HTML parse failed {qualified}: {e}")
            except Exception as e:
                errors.append(f"parse failed {qualified}: {e}")
    return out, dates, files_seen, errors

def parse_report_zip(content: bytes, catalog: dict, report_label: str, source_url: str) -> Tuple[Dict[str, dict], dict]:
    match_title = title_matcher(catalog)
    all_rows, asof_by_slug, files_seen, parse_errors = parse_archive_members(
        content, "report.zip", match_title
    )

    # Fallback report month date if exact date is not printed in the underlying file.
    m = re.search(r"([A-Z][a-z]{2})\s+(20\d{2})", report_label)
    fallback_asof = None
    if m:
        try:
            month = MONTH_ABBR.index(m.group(1)) + 1
            year = int(m.group(2))
            fallback_asof = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
        except Exception:
            pass

    results: Dict[str, dict] = {}
    catalog_by_slug = catalog_index_map(catalog)
    for slug, item in catalog_by_slug.items():
        holdings, coverage = dedupe_and_validate(all_rows.get(slug, []))
        if not holdings:
            continue
        completeness = "full" if 97 <= coverage <= 103 else "partial"
        top10 = sum(h["weight"] for h in holdings[:10])
        data = {
            "slug": slug,
            "index_name": item.get("name"),
            "as_of": asof_by_slug.get(slug) or fallback_asof,
            "report_month": report_label,
            "source": "NSE Indices — Indices Market Capitalisation & Weightage",
            "source_url": source_url,
            "source_type": "official_nse_indices_monthly_report",
            "completeness": completeness,
            "stock_count": len(holdings),
            "weight_coverage": round(coverage, 4),
            "top10_weight": round(top10, 4),
            "coverage_note": (
                "Official NSE Indices monthly constituent-weight report."
                if completeness == "full"
                else f"Official report parsed, but extracted weights cover {coverage:.1f}% of the index. Treat this view as partial until a fuller table is available."
            ),
            "holdings": holdings,
            "sectors": sector_rows(holdings),
        }
        results[slug] = data

    diagnostics = {
        "report_label": report_label,
        "source_url": source_url,
        "files_seen": files_seen,
        "parse_errors": parse_errors,
        "raw_rows_by_slug": {k: len(v) for k, v in sorted(all_rows.items())},
        "indices_parsed": len(results),
        "catalog_indices": len(catalog_by_slug),
        "missing_slugs": [s for s in catalog_by_slug if s not in results],
    }
    return results, diagnostics


def purge_demo_files(comp_dir: Path):
    for p in comp_dir.glob("*.json"):
        if p.name in {"manifest.json", "status.json"}:
            continue
        d = safe_json(p)
        if d and d.get("source_type") == "sample":
            print(f"Removing old demo composition file: {p.name}")
            p.unlink(missing_ok=True)


def build_manifest(catalog: dict, comp_dir: Path, parsed: Dict[str, dict], source_url: Optional[str], report_label: Optional[str], keep_existing: bool) -> dict:
    entries = {}
    for item in catalog.get("items", []):
        slug = item["slug"]
        path = comp_dir / f"{slug}.json"
        data = parsed.get(slug)
        if data:
            atomic_json(path, data)
        elif keep_existing and path.exists():
            old = safe_json(path)
            if old and str(old.get("source_type", "")).startswith("official"):
                data = old
        if data:
            entries[slug] = {
                "file": f"{slug}.json",
                "name": item.get("name"),
                "available": True,
                "as_of": data.get("as_of"),
                "retrieved_on": data.get("retrieved_on"),
                "completeness": data.get("completeness"),
                "stock_count": data.get("stock_count"),
                "weight_coverage": data.get("weight_coverage"),
                "top10_weight": data.get("top10_weight"),
                "source_type": data.get("source_type"),
            }
        else:
            entries[slug] = {
                "name": item.get("name"),
                "available": False,
                "reason": "An official constituent list could not be retrieved for this index.",
            }
    return {
        "version": 3,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "report_month": report_label,
        "source_url": source_url,
        "indices": entries,
    }


def autosize(ws, max_width: int = 42):
    for col in range(1, ws.max_column + 1):
        letter = get_column_letter(col)
        width = 0
        for row in range(1, min(ws.max_row, 600) + 1):
            v = ws.cell(row, col).value
            if v is not None:
                width = max(width, len(str(v)))
        ws.column_dimensions[letter].width = min(max(10, width + 2), max_width)


def update_workbook_composition(book_path: Path, data: Optional[dict]):
    try:
        wb = load_workbook(book_path)
    except Exception as e:
        print(f"WARNING: unable to open workbook {book_path}: {e}")
        return
    if "Composition" in wb.sheetnames:
        del wb["Composition"]
    ws = wb.create_sheet("Composition")
    ws.sheet_view.showGridLines = False
    dark = PatternFill("solid", fgColor="071A2D")
    header = PatternFill("solid", fgColor="0F2742")
    white = Font(color="FFFFFF", bold=True)
    muted = Font(color="666666")
    green = Font(color="008000")

    title = (data or {}).get("index_name") or book_path.stem.replace("-", " ").title()
    ws.merge_cells("A1:E1")
    ws["A1"] = f"{title} Composition"
    ws["A1"].fill = dark
    ws["A1"].font = Font(color="FFFFFF", bold=True, size=15)
    if not data:
        ws["A3"] = "Official composition data is not currently available for this index."
        ws["A3"].font = muted
        autosize(ws)
        wb.save(book_path)
        return

    ws["A2"] = "As of"
    ws["B2"] = data.get("as_of") or (f"Retrieved {data['retrieved_on']}" if data.get("retrieved_on") else data.get("report_month"))
    ws["D2"] = "Weight coverage"
    has_weights = data.get("weight_coverage") is not None
    ws["E2"] = float(data["weight_coverage"]) / 100 if has_weights else "Not published"
    if has_weights:
        ws["E2"].number_format = "0.0%"
    ws["A3"] = "Source"
    ws["B3"] = data.get("source")
    ws["B3"].hyperlink = data.get("source_url")
    ws["B3"].style = "Hyperlink"
    ws["D3"] = "Top 10 weight"
    ws["E3"] = float(data["top10_weight"]) / 100 if has_weights else "Not published"
    if has_weights:
        ws["E3"].number_format = "0.0%"
    for c in ("A2", "D2", "A3", "D3"):
        ws[c].font = muted
    for c in ("B2", "E2", "E3"):
        ws[c].font = green

    headers = ["Rank", "Company", "Symbol", "Sector / Industry", "Weight"]
    for i, h in enumerate(headers, 1):
        cell = ws.cell(5, i, h)
        cell.fill = header
        cell.font = white
        cell.alignment = Alignment(horizontal="center")
    for r, h in enumerate(data.get("holdings", []), start=6):
        ws.cell(r, 1, r - 5)
        ws.cell(r, 2, h.get("name"))
        ws.cell(r, 3, h.get("symbol"))
        ws.cell(r, 4, h.get("sector"))
        if h.get("weight") is not None:
            ws.cell(r, 5, float(h["weight"]) / 100)
            ws.cell(r, 5).number_format = "0.00%"
    ws.freeze_panes = "A6"
    autosize(ws)

    # Put Composition before Sources & Methodology if that sheet exists.
    if "Sources & Methodology" in wb.sheetnames:
        comp = wb["Composition"]
        src = wb["Sources & Methodology"]
        wb._sheets.remove(comp)
        idx = wb._sheets.index(src)
        wb._sheets.insert(idx, comp)
    wb.save(book_path)


def update_all_workbooks(web_dir: Path, catalog: dict, manifest: dict):
    out_dir = web_dir / "downloads" / "indices"
    if not out_dir.exists():
        return
    comp_dir = web_dir / "data" / "composition"
    for item in catalog.get("items", []):
        slug = item["slug"]
        book = out_dir / f"{slug}.xlsx"
        if not book.exists():
            continue
        ent = manifest.get("indices", {}).get(slug, {})
        data = safe_json(comp_dir / ent.get("file", "")) if ent.get("file") else None
        update_workbook_composition(book, data)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--web-dir", default="docs")
    p.add_argument("--max-months", type=int, default=8)
    p.add_argument("--strict", action="store_true", help="Exit non-zero if the official report cannot be refreshed")
    p.add_argument("--report-zip", help="Optional local report ZIP for testing/manual import")
    p.add_argument("--no-workbooks", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    web_dir = Path(args.web_dir)
    catalog_path = web_dir / "data" / "multi_index_catalog.json"
    if not catalog_path.exists():
        print(f"Composition: catalog not found at {catalog_path}; run multi_index_tracker.py first.")
        return 1 if args.strict else 0
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    comp_dir = web_dir / "data" / "composition"
    comp_dir.mkdir(parents=True, exist_ok=True)
    purge_demo_files(comp_dir)

    existing_manifest = safe_json(comp_dir / "manifest.json") or {"indices": {}}
    parsed = {}
    diagnostics = {"status": "not_refreshed"}
    source_url = None
    report_label = None
    try:
        if args.report_zip:
            local = Path(args.report_zip)
            content = local.read_bytes()
            if not is_zip_bytes(content):
                raise RuntimeError(f"{local} is not a valid ZIP file")
            source_url = f"file://{local.resolve()}"
            # Best-effort report label from filename, otherwise current month.
            m = re.search(r"([A-Z][a-z]{2})(20\d{2})", local.name)
            report_label = f"{m.group(1)} {m.group(2)}" if m else f"{MONTH_ABBR[date.today().month-1]} {date.today().year}"
            print(f"Composition: importing local report {local} ({len(content):,} bytes)")
        else:
            content, source_url, report_label = fetch_report_zip(date.today(), args.max_months)
            print(f"Composition: downloaded {report_label} report from {source_url} ({len(content):,} bytes)")
        parsed, diagnostics = parse_report_zip(content, catalog, report_label, source_url)
        diagnostics["status"] = "ok"
        print(f"Composition: parsed {len(parsed)}/{len(catalog.get('items', []))} tracked indices")
    except Exception as e:
        diagnostics = {"status": "refresh_failed", "error": str(e)}
        print(f"WARNING: composition refresh failed: {e}")
        if args.strict:
            return 2

    # The monthly report currently publishes only a few indices. Discover the
    # remaining official constituent lists from each index's own download link.
    # When the ZIP fails entirely, retain previously stored monthly weights.
    protected = dict(parsed)
    if diagnostics["status"] == "refresh_failed":
        for slug, entry in existing_manifest.get("indices", {}).items():
            if entry.get("source_type") == "official_nse_indices_monthly_report" and entry.get("available"):
                protected[slug] = True
    constituents, csv_errors = fetch_missing_constituents(catalog, protected)
    parsed.update(constituents)
    diagnostics["constituent_csv_indices"] = len(constituents)
    diagnostics["constituent_csv_errors"] = csv_errors
    if diagnostics["status"] == "refresh_failed" and constituents:
        diagnostics["status"] = "partial"
    print(f"Composition: fetched {len(constituents)} official constituent CSVs")

    manifest = build_manifest(
        catalog,
        comp_dir,
        parsed,
        source_url or existing_manifest.get("source_url"),
        report_label or existing_manifest.get("report_month"),
        keep_existing=True,
    )
    atomic_json(comp_dir / "manifest.json", manifest)
    status = {
        **diagnostics,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "available_indices": sum(1 for x in manifest["indices"].values() if x.get("available")),
        "tracked_indices": len(catalog.get("items", [])),
    }
    atomic_json(comp_dir / "status.json", status)

    if not args.no_workbooks:
        update_all_workbooks(web_dir, catalog, manifest)
        print("Composition: Excel Composition sheets refreshed.")

    print(f"Composition: {status['available_indices']}/{status['tracked_indices']} indices available in manifest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
