"""Point-in-time-safe session event feature construction."""

from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
import pandas as pd


def _window(bars: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    timestamps = bars["timestamp"]
    left = timestamps.searchsorted(start, side="left")
    right = timestamps.searchsorted(end, side="left")
    return bars.iloc[left:right]


def _expected_bars(minutes: int) -> int:
    if minutes <= 0 or minutes % 5:
        raise ValueError("Event windows must be positive and M5-aligned")
    return minutes // 5


def _coverage(frame: pd.DataFrame, minutes: int) -> float:
    return frame["timestamp"].nunique() / _expected_bars(minutes)


def _realized_volatility_pips(frame: pd.DataFrame, pip_size: float) -> float:
    if frame.empty:
        return math.nan
    prices = np.concatenate(
        ([float(frame.iloc[0]["mid_open"])], frame["mid_close"].to_numpy())
    )
    changes = np.diff(prices) / pip_size
    return float(np.sqrt(np.square(changes).sum()))


def build_event_dataset(
    bars: pd.DataFrame,
    calendar: pd.DataFrame,
    *,
    pip_size: float,
    preopen_windows: tuple[int, ...],
    horizons: tuple[int, ...],
    minimum_coverage_ratio: float,
    event_kind: str = "session_open",
) -> pd.DataFrame:
    """Create one row per supplied event with pre-open features and future labels."""

    if bars.empty:
        raise ValueError("Cannot build events from empty M5 data")
    if not 0 < minimum_coverage_ratio <= 1:
        raise ValueError("minimum_coverage_ratio must be in (0, 1]")
    bars = bars.copy()
    bars["timestamp"] = pd.to_datetime(bars["timestamp"], utc=True)
    bars = bars.sort_values("timestamp", kind="stable")
    rows = []

    for event in calendar.itertuples(index=False):
        opened = pd.Timestamp(event.open_timestamp_utc)
        open_position = bars["timestamp"].searchsorted(opened, side="left")
        open_bar = bars.iloc[open_position : open_position + 1]
        if open_bar.empty or open_bar.iloc[0]["timestamp"] != opened:
            open_bar = bars.iloc[0:0]
        reference = (
            float(open_bar.iloc[0]["mid_open"]) if len(open_bar) == 1 else math.nan
        )
        row = {
            "event_id": (
                f"{event_kind}:{event.session_name}:"
                f"{event.local_session_date.isoformat()}"
            ),
            "event_kind": event_kind,
            "session_name": event.session_name,
            "local_session_date": event.local_session_date,
            "event_timestamp_utc": opened,
            "open_timestamp_local": event.open_timestamp_local,
            "utc_offset_minutes": event.utc_offset_minutes,
            "is_dst": event.is_dst,
            "weekday": event.weekday,
            "calendar_year": event.local_session_date.year,
            "calendar_month": event.local_session_date.month,
            "fx_trading_day": event.fx_trading_day,
            "open_price_mid": reference,
            "open_bar_available": len(open_bar) == 1,
            "matched_event_id": getattr(event, "matched_event_id", None),
        }
        coverage_values = []

        for minutes in preopen_windows:
            frame = _window(bars, opened - timedelta(minutes=minutes), opened)
            coverage = _coverage(frame, minutes)
            coverage_values.append(coverage)
            prefix = f"pre_{minutes}"
            row[f"{prefix}_coverage"] = coverage
            row[f"{prefix}_range_pips"] = (
                float((frame["mid_high"].max() - frame["mid_low"].min()) / pip_size)
                if not frame.empty
                else math.nan
            )
            signed = (
                float(
                    (frame.iloc[-1]["mid_close"] - frame.iloc[0]["mid_open"]) / pip_size
                )
                if not frame.empty
                else math.nan
            )
            row[f"{prefix}_return_pips"] = signed
            row[f"{prefix}_abs_return_pips"] = abs(signed)
            row[f"{prefix}_realized_vol_pips"] = _realized_volatility_pips(
                frame, pip_size
            )
            row[f"{prefix}_tick_count"] = int(frame["tick_count"].sum())
            row[f"{prefix}_spread_median_pips"] = (
                float(frame["spread_median_pips"].median())
                if not frame.empty
                else math.nan
            )

        for minutes in horizons:
            frame = _window(bars, opened, opened + timedelta(minutes=minutes))
            coverage = _coverage(frame, minutes)
            coverage_values.append(coverage)
            prefix = f"fwd_{minutes}"
            row[f"{prefix}_coverage"] = coverage
            close_return = (
                float((frame.iloc[-1]["mid_close"] - reference) / pip_size)
                if not frame.empty and not math.isnan(reference)
                else math.nan
            )
            upward = (
                float((frame["mid_high"].max() - reference) / pip_size)
                if not frame.empty and not math.isnan(reference)
                else math.nan
            )
            downward = (
                float((reference - frame["mid_low"].min()) / pip_size)
                if not frame.empty and not math.isnan(reference)
                else math.nan
            )
            row[f"{prefix}_return_pips"] = close_return
            row[f"{prefix}_abs_return_pips"] = abs(close_return)
            row[f"{prefix}_range_pips"] = (
                float((frame["mid_high"].max() - frame["mid_low"].min()) / pip_size)
                if not frame.empty
                else math.nan
            )
            row[f"{prefix}_up_excursion_pips"] = upward
            row[f"{prefix}_down_excursion_pips"] = downward
            row[f"{prefix}_mfe_long_pips"] = upward
            row[f"{prefix}_mae_long_pips"] = downward
            row[f"{prefix}_mfe_short_pips"] = downward
            row[f"{prefix}_mae_short_pips"] = upward
            row[f"{prefix}_tick_count"] = int(frame["tick_count"].sum())
            row[f"{prefix}_spread_median_pips"] = (
                float(frame["spread_median_pips"].median())
                if not frame.empty
                else math.nan
            )

        denominator = row.get("pre_60_range_pips", math.nan)
        for minutes in horizons:
            prefix = f"fwd_{minutes}"
            for metric in (
                "range_pips",
                "abs_return_pips",
                "up_excursion_pips",
                "down_excursion_pips",
            ):
                value = row[f"{prefix}_{metric}"]
                row[f"{prefix}_{metric.removesuffix('_pips')}_over_pre60"] = (
                    value / denominator
                    if denominator and not math.isnan(denominator)
                    else math.nan
                )

        row["minimum_coverage"] = min(coverage_values)
        row["eligible"] = bool(
            row["open_bar_available"]
            and row["minimum_coverage"] >= minimum_coverage_ratio
        )
        row["exclusion_reason"] = (
            None
            if row["eligible"]
            else "missing_open_bar"
            if not row["open_bar_available"]
            else "insufficient_window_coverage"
        )
        rows.append(row)
    return pd.DataFrame(rows)
