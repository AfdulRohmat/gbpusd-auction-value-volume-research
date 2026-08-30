"""Canonical tick-to-M5 transformation."""

from __future__ import annotations

import pandas as pd


def resample_ticks_m5(ticks: pd.DataFrame) -> pd.DataFrame:
    if ticks.empty:
        raise ValueError("Cannot resample an empty tick dataset")
    if not ticks["timestamp"].is_monotonic_increasing:
        ticks = ticks.sort_values("timestamp", kind="stable")

    indexed = ticks.set_index("timestamp")
    if "mid_direction" not in indexed:
        indexed["mid_direction"] = indexed["mid"].diff().fillna(0).apply(
            lambda value: 1 if value > 0 else (-1 if value < 0 else 0)
        )
    if "bid_changed" not in indexed:
        indexed["bid_changed"] = indexed["bid"].ne(indexed["bid"].shift()).astype(
            "int8"
        )
    if "ask_changed" not in indexed:
        indexed["ask_changed"] = indexed["ask"].ne(indexed["ask"].shift()).astype(
            "int8"
        )
    indexed["_up_quote"] = indexed["mid_direction"].gt(0).astype("int8")
    indexed["_down_quote"] = indexed["mid_direction"].lt(0).astype("int8")
    indexed["_flat_quote"] = indexed["mid_direction"].eq(0).astype("int8")
    indexed["_tick_timestamp"] = indexed.index
    indexed["_mid_activity"] = indexed["mid"] * indexed["activity"]
    indexed["_mid_squared_activity"] = (
        indexed["mid"] * indexed["mid"] * indexed["activity"]
    )
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
            grouped["_up_quote"].sum(min_count=1).rename("up_quote_count"),
            grouped["_down_quote"].sum(min_count=1).rename("down_quote_count"),
            grouped["_flat_quote"].sum(min_count=1).rename("flat_quote_count"),
            grouped["bid_changed"].sum(min_count=1).rename("bid_change_count"),
            grouped["ask_changed"].sum(min_count=1).rename("ask_change_count"),
            grouped["_mid_activity"].sum(min_count=1).rename("mid_activity_sum"),
            grouped["_mid_squared_activity"]
            .sum(min_count=1)
            .rename("mid_squared_activity_sum"),
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
