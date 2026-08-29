"""Previous-day tick-activity Volume Profile construction."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from gbpusd_research.config import ProjectConfig, ValueStateConfig
from gbpusd_research.data.histdata import archive_path, iter_archive_ticks
from gbpusd_research.data.pipeline import iter_months
from gbpusd_research.features.vwap import assign_fx_trading_days
from gbpusd_research.utils.paths import resolve_within_project


def calculate_value_area(
    node_activity: dict[int, int],
    *,
    bin_size_price: float,
    value_area_fraction: float,
) -> dict[str, Any]:
    """Calculate deterministic POC/VAL/VAH from integer price-bin activity."""

    if not node_activity or sum(node_activity.values()) <= 0:
        raise ValueError("Profile nodes must contain positive activity")
    if not 0 < value_area_fraction <= 1:
        raise ValueError("value_area_fraction must be in (0, 1]")
    bins = sorted(node_activity)
    total = sum(node_activity.values())
    weighted_mean = sum(index * node_activity[index] for index in bins) / total
    maximum = max(node_activity.values())
    candidates = [index for index in bins if node_activity[index] == maximum]
    poc = min(candidates, key=lambda index: (abs(index - weighted_mean), index))
    position = bins.index(poc)
    lower = position - 1
    upper = position + 1
    selected = {poc}
    selected_activity = node_activity[poc]
    target = total * value_area_fraction
    while selected_activity < target and (lower >= 0 or upper < len(bins)):
        lower_activity = node_activity[bins[lower]] if lower >= 0 else -1
        upper_activity = node_activity[bins[upper]] if upper < len(bins) else -1
        if lower_activity >= upper_activity:
            chosen = bins[lower]
            lower -= 1
        else:
            chosen = bins[upper]
            upper += 1
        selected.add(chosen)
        selected_activity += node_activity[chosen]
    return {
        "poc": poc * bin_size_price,
        "val": min(selected) * bin_size_price,
        "vah": max(selected) * bin_size_price,
        "value_area_activity_fraction": selected_activity / total,
        "total_activity": total,
        "node_count": len(bins),
    }


def build_daily_tick_profiles(
    project_root: Path,
    config: ProjectConfig,
    value_config: ValueStateConfig,
    bars: pd.DataFrame,
) -> pd.DataFrame:
    """Stream configured archives into one profile per New-York-close FX day."""

    research = config.research
    raw_root = resolve_within_project(project_root, research.data.paths.raw)
    bin_size_price = value_config.profile.bin_size_pips * research.instrument.pip_size
    start_utc = pd.Timestamp(
        datetime.combine(research.data.start, datetime.min.time(), tzinfo=UTC)
    )
    end_utc = pd.Timestamp(
        datetime.combine(research.data.end, datetime.min.time(), tzinfo=UTC)
    )
    nodes: dict[object, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    first_timestamp: dict[object, pd.Timestamp] = {}
    last_timestamp: dict[object, pd.Timestamp] = {}

    for year, month in iter_months(research.data.start, research.data.end):
        source = archive_path(raw_root, research.instrument.symbol, year, month)
        if not source.is_file():
            raise ValueError(f"Missing HistData archive: {source}")
        for chunk in iter_archive_ticks(source, pip_size=research.instrument.pip_size):
            chunk = chunk[
                chunk["timestamp"].ge(start_utc) & chunk["timestamp"].lt(end_utc)
            ].copy()
            if chunk.empty:
                continue
            chunk["fx_trading_day"] = assign_fx_trading_days(
                chunk["timestamp"], config.sessions.trading_day
            )
            chunk["price_bin"] = np.floor(chunk["mid"] / bin_size_price + 0.5).astype(
                "int64"
            )
            aggregated = chunk.groupby(["fx_trading_day", "price_bin"], observed=True)[
                "activity"
            ].sum()
            for (day, price_bin), activity in aggregated.items():
                nodes[day][int(price_bin)] += int(activity)
            for day, group in chunk.groupby("fx_trading_day", observed=True):
                observed_first = group["timestamp"].min()
                observed_last = group["timestamp"].max()
                first_timestamp[day] = min(
                    first_timestamp.get(day, observed_first), observed_first
                )
                last_timestamp[day] = max(
                    last_timestamp.get(day, observed_last), observed_last
                )

    bar_days = bars.copy()
    bar_days["timestamp"] = pd.to_datetime(bar_days["timestamp"], utc=True)
    bar_days["fx_trading_day"] = assign_fx_trading_days(
        bar_days["timestamp"], config.sessions.trading_day
    )
    m5_counts = bar_days.groupby("fx_trading_day", observed=True)["timestamp"].nunique()

    rows = []
    for day in sorted(nodes):
        profile = calculate_value_area(
            nodes[day],
            bin_size_price=bin_size_price,
            value_area_fraction=value_config.profile.value_area_fraction,
        )
        observed_m5 = int(m5_counts.get(day, 0))
        coverage = observed_m5 / 288
        rows.append(
            {
                "profile_day": day,
                **profile,
                "value_width_pips": (profile["vah"] - profile["val"])
                / research.instrument.pip_size,
                "observed_m5_bars": observed_m5,
                "expected_m5_bars": 288,
                "m5_coverage_ratio": coverage,
                "eligible": coverage >= value_config.profile.minimum_m5_coverage_ratio,
                "first_tick_timestamp": first_timestamp[day],
                "last_tick_timestamp": last_timestamp[day],
            }
        )
    return pd.DataFrame(rows).sort_values("profile_day").reset_index(drop=True)


def attach_previous_profile(
    events: pd.DataFrame,
    profiles: pd.DataFrame,
    *,
    pip_size: float,
    boundary_buffer_pips: float,
) -> pd.DataFrame:
    """Join the most recent eligible profile completed before each event day."""

    eligible = profiles[profiles["eligible"]].sort_values("profile_day")
    rows = []
    for event in events.itertuples(index=False):
        candidates = eligible[eligible["profile_day"].lt(event.fx_trading_day)]
        if candidates.empty:
            rows.append({"profile_available": False})
            continue
        profile = candidates.iloc[-1]
        open_price = event.open_price_mid
        buffer_price = boundary_buffer_pips * pip_size
        state = (
            "above_value"
            if open_price > profile["vah"] + buffer_price
            else "below_value"
            if open_price < profile["val"] - buffer_price
            else "inside_value"
        )
        rows.append(
            {
                "profile_available": True,
                "previous_profile_day": profile["profile_day"],
                "previous_poc": profile["poc"],
                "previous_vah": profile["vah"],
                "previous_val": profile["val"],
                "previous_value_width_pips": profile["value_width_pips"],
                "previous_profile_activity": profile["total_activity"],
                "previous_profile_m5_coverage": profile["m5_coverage_ratio"],
                "distance_to_poc_pips": (open_price - profile["poc"]) / pip_size,
                "distance_to_vah_pips": (open_price - profile["vah"]) / pip_size,
                "distance_to_val_pips": (open_price - profile["val"]) / pip_size,
                "value_state": state,
            }
        )
    return pd.concat([events.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
