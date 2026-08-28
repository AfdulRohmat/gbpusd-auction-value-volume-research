"""Canonical tick-to-M5 transformation."""

from __future__ import annotations

import pandas as pd


def resample_ticks_m5(ticks: pd.DataFrame) -> pd.DataFrame:
    if ticks.empty:
        raise ValueError("Cannot resample an empty tick dataset")
    if not ticks["timestamp"].is_monotonic_increasing:
        ticks = ticks.sort_values("timestamp", kind="stable")

    indexed = ticks.set_index("timestamp")
    indexed["_tick_timestamp"] = indexed.index
    output: list[pd.DataFrame | pd.Series] = []
    for side in ("bid", "ask", "mid"):
        ohlc = indexed[side].resample("5min", label="left", closed="left").ohlc()
        ohlc.columns = [f"{side}_{column}" for column in ohlc.columns]
        output.append(ohlc)

    grouped = indexed.resample("5min", label="left", closed="left")
    output.extend(
        [
            grouped.size().rename("tick_count"),
            grouped["activity"].sum(min_count=1).rename("activity_count"),
            grouped["spread_pips"].first().rename("spread_open_pips"),
            grouped["spread_pips"].median().rename("spread_median_pips"),
            grouped["spread_pips"].mean().rename("spread_mean_pips"),
            grouped["spread_pips"].quantile(0.95).rename("spread_p95_pips"),
            grouped["spread_pips"].max().rename("spread_max_pips"),
            grouped["source_archive"].first().rename("source_archive"),
        ]
    )
    bars = pd.concat(output, axis=1)
    bars = bars[bars["tick_count"] > 0].copy()

    first_ticks = grouped["_tick_timestamp"].min().rename("first_tick_timestamp")
    last_ticks = grouped["_tick_timestamp"].max().rename("last_tick_timestamp")
    bars = bars.join(first_ticks).join(last_ticks)
    bars.insert(0, "timestamp", bars.index)
    return bars.reset_index(drop=True)
