"""Point-in-time tick-activity VWAP features for Phase 2."""

from __future__ import annotations

import numpy as np
import pandas as pd

from gbpusd_research.config import TradingDayConfig


def assign_fx_trading_days(
    timestamps: pd.Series, config: TradingDayConfig
) -> pd.Series:
    """Vectorized equivalent of the configured New-York-close day label."""

    utc = pd.to_datetime(timestamps, utc=True)
    local = utc.dt.tz_convert(config.timezone)
    after_boundary = (local.dt.hour > config.boundary.hour) | (
        local.dt.hour.eq(config.boundary.hour)
        & (
            (local.dt.minute > config.boundary.minute)
            | (
                local.dt.minute.eq(config.boundary.minute)
                & (local.dt.second >= config.boundary.second)
            )
        )
    )
    local_dates = local.dt.tz_localize(None).dt.normalize()
    labels = local_dates + pd.to_timedelta(after_boundary.astype("int8"), unit="D")
    return labels.dt.date


def enrich_fx_day_vwap(
    bars: pd.DataFrame,
    trading_day: TradingDayConfig,
    *,
    pip_size: float,
    slope_minutes: int,
) -> pd.DataFrame:
    """Add exact cumulative VWAP moments and an M5-close availability time."""

    required = {
        "timestamp",
        "activity_count",
        "mid_activity_sum",
        "mid_squared_activity_sum",
    }
    missing = sorted(required.difference(bars.columns))
    if missing:
        raise ValueError("VWAP input is missing columns: " + ", ".join(missing))
    if slope_minutes <= 0 or slope_minutes % 5:
        raise ValueError("slope_minutes must be positive and M5-aligned")

    enriched = bars.copy()
    enriched["timestamp"] = pd.to_datetime(enriched["timestamp"], utc=True)
    enriched = enriched.sort_values("timestamp", kind="stable").reset_index(drop=True)
    enriched["fx_trading_day"] = assign_fx_trading_days(
        enriched["timestamp"], trading_day
    )
    grouped = enriched.groupby("fx_trading_day", sort=False, observed=True)
    enriched["vwap_cumulative_activity"] = grouped["activity_count"].cumsum()
    enriched["vwap_cumulative_mid_sum"] = grouped["mid_activity_sum"].cumsum()
    enriched["vwap_cumulative_mid_squared_sum"] = grouped[
        "mid_squared_activity_sum"
    ].cumsum()
    enriched["fx_day_vwap"] = (
        enriched["vwap_cumulative_mid_sum"] / enriched["vwap_cumulative_activity"]
    )
    second_moment = (
        enriched["vwap_cumulative_mid_squared_sum"]
        / enriched["vwap_cumulative_activity"]
    )
    variance = (second_moment - enriched["fx_day_vwap"] ** 2).clip(lower=0)
    enriched["fx_day_vwap_std_pips"] = np.sqrt(variance) / pip_size
    enriched["vwap_available_at"] = enriched["timestamp"] + pd.Timedelta(5, unit="min")

    lookup = {
        (day, timestamp): value
        for day, timestamp, value in enriched[
            ["fx_trading_day", "timestamp", "fx_day_vwap"]
        ].itertuples(index=False, name=None)
    }
    delta = pd.Timedelta(slope_minutes, unit="min")
    previous = [
        lookup.get((day, timestamp - delta), np.nan)
        for day, timestamp in enriched[["fx_trading_day", "timestamp"]].itertuples(
            index=False, name=None
        )
    ]
    enriched["vwap_slope_pips"] = (
        enriched["fx_day_vwap"] - np.asarray(previous)
    ) / pip_size
    return enriched


def attach_event_vwap(
    events: pd.DataFrame,
    enriched_bars: pd.DataFrame,
    *,
    pip_size: float,
    boundary_buffer_pips: float,
) -> pd.DataFrame:
    """Attach the latest VWAP whose M5 bar was complete at each event time."""

    output = events.copy()
    feature_rows = []
    by_day = {
        day: group.sort_values("vwap_available_at", kind="stable")
        for day, group in enriched_bars.groupby(
            "fx_trading_day", sort=False, observed=True
        )
    }
    for event in output.itertuples(index=False):
        opened = pd.Timestamp(event.event_timestamp_utc)
        candidates = by_day.get(event.fx_trading_day)
        if candidates is None:
            feature_rows.append({"vwap_available": False})
            continue
        available = candidates[candidates["vwap_available_at"].le(opened)]
        if available.empty:
            feature_rows.append({"vwap_available": False})
            continue
        latest = available.iloc[-1]
        distance = (event.open_price_mid - latest["fx_day_vwap"]) / pip_size
        standard_deviation = latest["fx_day_vwap_std_pips"]
        state = (
            "above_vwap"
            if distance > boundary_buffer_pips
            else "below_vwap"
            if distance < -boundary_buffer_pips
            else "at_vwap"
        )
        feature_rows.append(
            {
                "vwap_available": True,
                "vwap_available_at": latest["vwap_available_at"],
                "fx_day_vwap": latest["fx_day_vwap"],
                "vwap_distance_pips": distance,
                "vwap_zscore": (
                    distance / standard_deviation
                    if standard_deviation and not np.isnan(standard_deviation)
                    else np.nan
                ),
                "vwap_slope_pips": latest["vwap_slope_pips"],
                "vwap_state": state,
            }
        )
    return pd.concat(
        [output.reset_index(drop=True), pd.DataFrame(feature_rows)], axis=1
    )
