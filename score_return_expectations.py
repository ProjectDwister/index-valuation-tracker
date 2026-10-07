"""Historical forward returns from quarter-end observations near a live score.

This is separate from the valuation-quintile backtest. Historical scores use
only P/E observations available through each quarter and preserve the P/E
methodology regime boundary. Forward returns are price-only annualised returns.
"""
from __future__ import annotations

from statistics import median
from typing import Callable

import pandas as pd


HORIZONS = (1, 3, 5, 10)
SCORE_WINDOW = 25.0
MIN_MATCHES = 5
MAX_MATCHES = 8


def historical_scores(
    detail: pd.DataFrame,
    growth_score: Callable[[float], float],
    min_expanding_obs: int = 8,
) -> pd.DataFrame:
    """Attach a score calculated without future P/E values to each quarter."""
    d = detail.copy()
    if d.empty:
        d["Historical_Composite_Score"] = pd.Series(dtype=float)
        return d
    d["Date"] = pd.to_datetime(d["Date"])
    d = d.sort_values("Date")

    # The canonical NIFTY 50 file already contains its long-history,
    # expanding-window scores. Keep those scores rather than recomputing them
    # from the shorter multi-index archive.
    if "Composite_Score" in d.columns:
        d["Historical_Composite_Score"] = pd.to_numeric(d["Composite_Score"], errors="coerce")
        return d

    history: dict[str, list[float]] = {}
    scores: list[float | None] = []
    for _, row in d.iterrows():
        regime = str(row["Regime"])
        pe = pd.to_numeric(row["PE"], errors="coerce")
        eps_growth = pd.to_numeric(row["EPS_Growth_YoY"], errors="coerce")
        if pd.isna(pe) or pe <= 0:
            scores.append(None)
            continue
        values = history.setdefault(regime, [])
        values.append(float(pe))
        if len(values) < min_expanding_obs or pd.isna(eps_growth):
            scores.append(None)
            continue
        pct = (sum(v < pe for v in values) + .5 * sum(abs(v - pe) < 1e-10 for v in values)) / len(values)
        scores.append(.70 * (100 * (1 - pct)) + .30 * growth_score(float(eps_growth)))
    d["Historical_Composite_Score"] = scores
    return d


def build_score_return_expectations(
    detail: pd.DataFrame,
    current_score: float | None,
    growth_score: Callable[[float], float],
    as_of: str | None = None,
    min_expanding_obs: int = 8,
) -> dict:
    """Use up to eight nearest scored quarters within 25 score points per horizon."""
    d = historical_scores(detail, growth_score, min_expanding_obs)
    result = {
        "as_of": as_of,
        "method": "nearest_historical_composite_score",
        "max_score_distance": SCORE_WINDOW,
        "min_samples": MIN_MATCHES,
        "max_samples": MAX_MATCHES,
        "horizons": {},
    }
    for years in HORIZONS:
        candidates = []
        if current_score is not None and pd.notna(current_score):
            for _, row in d.iterrows():
                score = row["Historical_Composite_Score"]
                ret = pd.to_numeric(row.get(f"Fwd_{years}Y"), errors="coerce")
                if pd.isna(score) or pd.isna(ret):
                    continue
                distance = abs(float(score) - float(current_score))
                if distance <= SCORE_WINDOW:
                    candidates.append((distance, row["Date"], float(score), float(ret)))
        candidates.sort(key=lambda item: (item[0], -item[1].value))
        matches = candidates[:MAX_MATCHES]
        result["horizons"][str(years)] = {
            "median_cagr": float(median(m[3] for m in matches)) if len(matches) >= MIN_MATCHES else None,
            "n": len(matches),
            "score_min": min((m[2] for m in matches), default=None),
            "score_max": max((m[2] for m in matches), default=None),
            "first_quarter": min((m[1] for m in matches), default=None).date().isoformat() if matches else None,
            "last_quarter": max((m[1] for m in matches), default=None).date().isoformat() if matches else None,
        }
    return result
