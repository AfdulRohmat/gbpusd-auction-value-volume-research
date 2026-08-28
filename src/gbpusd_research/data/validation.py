"""Data-quality checks with machine-readable summaries."""

from __future__ import annotations

from typing import Any

import pandas as pd


def validate_ticks(ticks: pd.DataFrame, *, max_spread_pips: float) -> dict[str, Any]:
    required = {
        "timestamp",
        "bid",
        "ask",
        "mid",
        "spread_pips",
        "activity",
    }
    missing = sorted(required.difference(ticks.columns))
    if missing:
        return {"valid": False, "missing_columns": missing, "row_count": len(ticks)}

    timestamp = ticks["timestamp"]
    timezone = getattr(timestamp.dt, "tz", None)
    duplicate_count = int(timestamp.duplicated().sum())
    crossed_count = int((ticks["bid"] > ticks["ask"]).sum())
    nonpositive_count = int(((ticks["bid"] <= 0) | (ticks["ask"] <= 0)).sum())
    nonpositive_activity_count = int((ticks["activity"] <= 0).sum())
    null_count = int(ticks[list(required)].isna().any(axis=1).sum())
    excessive_spread_count = int((ticks["spread_pips"] > max_spread_pips).sum())
    monotonic = bool(timestamp.is_monotonic_increasing)
    utc_aware = timezone is not None and str(timezone) == "UTC"

    return {
        "valid": bool(
            len(ticks)
            and utc_aware
            and monotonic
            and not crossed_count
            and not nonpositive_count
            and not nonpositive_activity_count
            and not null_count
        ),
        "row_count": len(ticks),
        "utc_aware": utc_aware,
        "monotonic": monotonic,
        "duplicate_timestamp_count": duplicate_count,
        "crossed_quote_count": crossed_count,
        "nonpositive_price_count": nonpositive_count,
        "nonpositive_activity_count": nonpositive_activity_count,
        "null_row_count": null_count,
        "excessive_spread_count": excessive_spread_count,
        "spread_pips": {
            "min": float(ticks["spread_pips"].min()) if len(ticks) else None,
            "median": float(ticks["spread_pips"].median()) if len(ticks) else None,
            "p95": float(ticks["spread_pips"].quantile(0.95)) if len(ticks) else None,
            "max": float(ticks["spread_pips"].max()) if len(ticks) else None,
        },
    }


def validate_m5(bars: pd.DataFrame) -> dict[str, Any]:
    violations: dict[str, int] = {}
    for side in ("bid", "ask", "mid"):
        high = bars[f"{side}_high"]
        low = bars[f"{side}_low"]
        opened = bars[f"{side}_open"]
        closed = bars[f"{side}_close"]
        invalid = (
            (high < low)
            | (high < opened)
            | (high < closed)
            | (low > opened)
            | (low > closed)
        )
        violations[side] = int(invalid.sum())

    return {
        "valid": bool(len(bars) and not sum(violations.values())),
        "bar_count": len(bars),
        "ohlc_violation_count": violations,
        "first_bar": bars["timestamp"].min().isoformat() if len(bars) else None,
        "last_bar": bars["timestamp"].max().isoformat() if len(bars) else None,
    }
