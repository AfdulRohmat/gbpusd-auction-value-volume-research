"""Continuous, non-trading auction-state taxonomy for Phase 7."""

from __future__ import annotations

import numpy as np
import pandas as pd

from gbpusd_research.config import AuctionTaxonomyConfig

STABLE_STATES = {"balance", "imbalance_up", "imbalance_down"}
M5_DELTA = np.timedelta64(5, "m")


def _clock_columns(available: pd.Series) -> pd.DataFrame:
    utc = pd.to_datetime(available, utc=True)
    london = utc.dt.tz_convert("Europe/London")
    new_york = utc.dt.tz_convert("America/New_York")
    london_minutes = london.dt.hour * 60 + london.dt.minute - 8 * 60
    new_york_minutes = new_york.dt.hour * 60 + new_york.dt.minute - 8 * 60
    bucket = np.select(
        [
            london_minutes.ge(0) & london_minutes.lt(60),
            new_york_minutes.ge(0) & new_york_minutes.lt(60),
            london_minutes.ge(-60) & london_minutes.lt(0),
            new_york_minutes.ge(-60) & new_york_minutes.lt(0),
        ],
        [
            "london_open_hour",
            "new_york_open_hour",
            "pre_london_hour",
            "pre_new_york_hour",
        ],
        default="other",
    )
    return pd.DataFrame(
        {
            "london_local_hour": london.dt.hour.to_numpy(),
            "new_york_local_hour": new_york.dt.hour.to_numpy(),
            "minutes_from_london_open": london_minutes.to_numpy(),
            "minutes_from_new_york_open": new_york_minutes.to_numpy(),
            "clock_bucket": bucket,
        },
        index=available.index,
    )


def _window_features(
    frame: pd.DataFrame,
    *,
    window_bars: int,
) -> dict[str, np.ndarray]:
    size = len(frame)
    displacement = np.full(size, np.nan)
    path = np.full(size, np.nan)
    efficiency = np.full(size, np.nan)
    overlap = np.full(size, np.nan)
    persistence = np.full(size, np.nan)
    midpoint_crossings = np.full(size, np.nan)
    close_location = np.full(size, np.nan)
    window_high = np.full(size, np.nan)
    window_low = np.full(size, np.nan)

    opens = frame["mid_open"].to_numpy(float)
    highs = frame["mid_high"].to_numpy(float)
    lows = frame["mid_low"].to_numpy(float)
    closes = frame["mid_close"].to_numpy(float)
    for end in range(window_bars - 1, size):
        start = end - window_bars + 1
        local_highs = highs[start : end + 1]
        local_lows = lows[start : end + 1]
        local_closes = closes[start : end + 1]
        high = float(local_highs.max())
        low = float(local_lows.min())
        net = float(local_closes[-1] - opens[start])
        moves = np.diff(np.concatenate(([opens[start]], local_closes)))
        traveled = float(np.abs(moves).sum())
        ranges = local_highs - local_lows
        adjacent = []
        for offset in range(1, window_bars):
            shared = max(
                0.0,
                min(local_highs[offset - 1], local_highs[offset])
                - max(local_lows[offset - 1], local_lows[offset]),
            )
            smaller = min(ranges[offset - 1], ranges[offset])
            adjacent.append(shared / smaller if smaller > 0 else 1.0)
        direction = np.sign(net)
        nonzero = moves[np.abs(moves) > 0]
        aligned = (
            float((np.sign(nonzero) == direction).mean())
            if direction and len(nonzero)
            else 0.0
        )
        midpoint = (high + low) / 2
        signs = np.sign(local_closes - midpoint)
        signs = signs[signs != 0]
        crossings = int((signs[1:] != signs[:-1]).sum()) if len(signs) > 1 else 0
        width = high - low

        displacement[end] = net
        path[end] = traveled
        efficiency[end] = abs(net) / traveled if traveled > 0 else 0.0
        overlap[end] = float(np.mean(adjacent))
        persistence[end] = aligned
        midpoint_crossings[end] = crossings
        close_location[end] = (
            (local_closes[-1] - low) / width if width > 0 else 0.5
        )
        window_high[end] = high
        window_low[end] = low

    return {
        "displacement_price": displacement,
        "path_price": path,
        "efficiency": efficiency,
        "mean_overlap": overlap,
        "directional_persistence": persistence,
        "midpoint_crossings": midpoint_crossings,
        "close_location": close_location,
        "window_high": window_high,
        "window_low": window_low,
    }


def _raw_states(frame: pd.DataFrame, config: AuctionTaxonomyConfig) -> pd.Series:
    state = config.state
    valid = frame["efficiency"].notna()
    balance = (
        valid
        & frame["efficiency"].le(state.balance_max_efficiency)
        & frame["mean_overlap"].ge(state.balance_min_overlap)
        & frame["midpoint_crossings"].ge(state.balance_min_midpoint_crossings)
    )
    up = (
        valid
        & frame["displacement_price"].gt(0)
        & frame["efficiency"].ge(state.imbalance_min_efficiency)
        & frame["directional_persistence"].ge(
            state.imbalance_min_directional_persistence
        )
        & frame["close_location"].ge(state.extreme_close_fraction)
    )
    down = (
        valid
        & frame["displacement_price"].lt(0)
        & frame["efficiency"].ge(state.imbalance_min_efficiency)
        & frame["directional_persistence"].ge(
            state.imbalance_min_directional_persistence
        )
        & frame["close_location"].le(1 - state.extreme_close_fraction)
    )
    values = np.select(
        [balance, up, down, valid],
        ["balance", "imbalance_up", "imbalance_down", "transition"],
        default="warmup",
    )
    return pd.Series(values, index=frame.index, dtype="string")


def build_state_timeline(
    bars: pd.DataFrame,
    config: AuctionTaxonomyConfig,
    *,
    pip_size: float,
    sample_year: int,
) -> pd.DataFrame:
    """Calculate point-in-time rolling state and independent activity features."""

    ordered = bars.copy()
    ordered["timestamp"] = pd.to_datetime(ordered["timestamp"], utc=True)
    ordered = ordered.sort_values("timestamp", kind="stable").drop_duplicates(
        "timestamp", keep="last"
    ).reset_index(drop=True)
    gaps = ordered["timestamp"].diff().ne(M5_DELTA)
    ordered["segment_number"] = gaps.cumsum().astype(int) - 1
    ordered["segment_id"] = (
        str(sample_year) + "-" + ordered["segment_number"].astype(str)
    )
    ordered["sample_year"] = sample_year
    ordered["available_at"] = ordered["timestamp"] + M5_DELTA
    columns = [
        "displacement_price",
        "path_price",
        "efficiency",
        "mean_overlap",
        "directional_persistence",
        "midpoint_crossings",
        "close_location",
        "window_high",
        "window_low",
    ]
    for column in columns:
        ordered[column] = np.nan
    window_bars = config.state.window_minutes // 5
    for _, segment in ordered.groupby("segment_number", sort=True):
        features = _window_features(segment, window_bars=window_bars)
        for column, values in features.items():
            ordered.loc[segment.index, column] = values

    ordered["window_range_pips"] = (
        ordered["window_high"] - ordered["window_low"]
    ) / pip_size
    ordered["displacement_pips"] = ordered["displacement_price"] / pip_size
    ordered["path_pips"] = ordered["path_price"] / pip_size
    ordered["raw_auction_state"] = _raw_states(ordered, config)
    baseline = ordered.groupby("segment_number", sort=False)[
        "window_range_pips"
    ].transform(
        lambda values: values.shift(1).rolling(
            config.activity.baseline_bars,
            min_periods=config.activity.minimum_baseline_bars,
        ).median()
    )
    ordered["activity_baseline_range_pips"] = baseline
    ordered["activity_ratio"] = ordered["window_range_pips"] / baseline
    ordered["activity_regime"] = np.select(
        [
            ordered["activity_ratio"].le(config.activity.quiet_ratio_max),
            ordered["activity_ratio"].ge(config.activity.active_ratio_min),
            ordered["activity_ratio"].notna(),
        ],
        ["quiet", "active", "normal"],
        default="unavailable",
    )
    clocks = _clock_columns(ordered["available_at"])
    for column in clocks:
        ordered[column] = clocks[column]
    keep = [
        "sample_year",
        "segment_id",
        "segment_number",
        "timestamp",
        "available_at",
        "mid_open",
        "mid_high",
        "mid_low",
        "mid_close",
        "tick_count",
        "spread_median_pips",
        "window_high",
        "window_low",
        "window_range_pips",
        "displacement_pips",
        "path_pips",
        "efficiency",
        "mean_overlap",
        "directional_persistence",
        "midpoint_crossings",
        "close_location",
        "raw_auction_state",
        "activity_baseline_range_pips",
        "activity_ratio",
        "activity_regime",
        "london_local_hour",
        "new_york_local_hour",
        "minutes_from_london_open",
        "minutes_from_new_york_open",
        "clock_bucket",
    ]
    return ordered[keep].copy()


def _dominant_activity(values: pd.Series) -> str:
    eligible = values[values.ne("unavailable")]
    if eligible.empty:
        return "unavailable"
    counts = eligible.value_counts()
    return str(sorted(counts[counts.eq(counts.max())].index)[0])


def build_state_episodes(
    timeline: pd.DataFrame,
    config: AuctionTaxonomyConfig,
    *,
    pip_size: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply confirmation hysteresis and emit persistent state episodes."""

    result = timeline.copy()
    result["observable_state"] = pd.Series(pd.NA, index=result.index, dtype="string")
    result["observable_episode_id"] = pd.Series(
        pd.NA, index=result.index, dtype="string"
    )
    result["episode_state"] = pd.Series(pd.NA, index=result.index, dtype="string")
    result["episode_id"] = pd.Series(pd.NA, index=result.index, dtype="string")
    episode_rows: list[dict[str, object]] = []

    for segment_id, segment in result.groupby("segment_id", sort=True):
        positions = list(segment.index)
        changes: list[dict[str, object]] = []
        current_state: str | None = None
        current_episode_id: str | None = None
        candidate: str | None = None
        candidate_start = -1
        streak = 0

        for local_position, index in enumerate(positions):
            raw = str(result.at[index, "raw_auction_state"])
            if raw in STABLE_STATES and raw != current_state:
                if raw == candidate:
                    streak += 1
                else:
                    candidate = raw
                    candidate_start = local_position
                    streak = 1
                if streak >= config.state.confirmation_windows:
                    current_state = raw
                    current_episode_id = f"{segment_id}-ep{len(changes):05d}"
                    changes.append(
                        {
                            "state": raw,
                            "episode_id": current_episode_id,
                            "start_local": candidate_start,
                            "confirmed_local": local_position,
                        }
                    )
                    candidate = None
                    candidate_start = -1
                    streak = 0
            else:
                candidate = None
                candidate_start = -1
                streak = 0
            if current_state is not None:
                result.at[index, "observable_state"] = current_state
                result.at[index, "observable_episode_id"] = current_episode_id

        for change_index, change in enumerate(changes):
            start_local = int(change["start_local"])
            end_local = (
                int(changes[change_index + 1]["start_local"])
                if change_index + 1 < len(changes)
                else len(positions)
            )
            indexes = positions[start_local:end_local]
            if not indexes:
                continue
            start_index = indexes[0]
            end_index = indexes[-1]
            start_at = pd.Timestamp(result.at[start_index, "available_at"])
            confirmed_index = positions[int(change["confirmed_local"])]
            confirmed_at = pd.Timestamp(result.at[confirmed_index, "available_at"])
            if change_index + 1 < len(changes):
                end_at = pd.Timestamp(
                    result.at[positions[end_local], "available_at"]
                )
            else:
                end_at = (
                    pd.Timestamp(result.at[end_index, "available_at"])
                    + M5_DELTA
                )
            episode_id = str(change["episode_id"])
            state = str(change["state"])
            result.loc[indexes, "episode_state"] = state
            result.loc[indexes, "episode_id"] = episode_id
            frame = result.loc[indexes]
            high = float(frame["mid_high"].max())
            low = float(frame["mid_low"].min())
            episode_rows.append(
                {
                    "sample_year": int(frame.iloc[0]["sample_year"]),
                    "segment_id": segment_id,
                    "episode_id": episode_id,
                    "state": state,
                    "direction": (
                        1
                        if state == "imbalance_up"
                        else -1
                        if state == "imbalance_down"
                        else 0
                    ),
                    "start_at": start_at,
                    "confirmed_at": confirmed_at,
                    "end_at": end_at,
                    "confirmation_latency_minutes": (
                        confirmed_at - start_at
                    ).total_seconds()
                    / 60,
                    "duration_minutes": (end_at - start_at).total_seconds() / 60,
                    "state_windows": len(frame),
                    "start_price": float(frame.iloc[0]["mid_close"]),
                    "end_price": float(frame.iloc[-1]["mid_close"]),
                    "episode_high": high,
                    "episode_low": low,
                    "episode_width_pips": (high - low) / pip_size,
                    "net_change_pips": (
                        float(frame.iloc[-1]["mid_close"])
                        - float(frame.iloc[0]["mid_close"])
                    )
                    / pip_size,
                    "dominant_activity_regime": _dominant_activity(
                        frame["activity_regime"]
                    ),
                    "start_activity_regime": str(
                        frame.iloc[0]["activity_regime"]
                    ),
                    "start_clock_bucket": str(frame.iloc[0]["clock_bucket"]),
                    "left_censored": change_index == 0,
                    "right_censored": change_index == len(changes) - 1,
                    "_start_index": start_index,
                    "_end_index": end_index,
                }
            )
    return result, pd.DataFrame(episode_rows)


def build_state_transitions(
    timeline: pd.DataFrame,
    episodes: pd.DataFrame,
    config: AuctionTaxonomyConfig,
    *,
    pip_size: float,
) -> pd.DataFrame:
    """Describe adjacent persistent-state changes and observable signatures."""

    rows: list[dict[str, object]] = []
    tolerance = config.transition.boundary_test_tolerance_pips * pip_size
    for segment_id, frame in episodes.groupby("segment_id", sort=True):
        ordered = frame.sort_values("start_at", kind="stable").reset_index(drop=True)
        for position in range(1, len(ordered)):
            previous = ordered.iloc[position - 1]
            current = ordered.iloc[position]
            start_index = int(current["_start_index"])
            trigger = timeline.loc[start_index]
            pre_index = start_index - 1
            pre = timeline.loc[pre_index] if pre_index in timeline.index else trigger
            trigger_close = float(trigger["mid_close"])
            boundary_break = False
            if previous["state"] == "balance" and current["state"] == "imbalance_up":
                boundary_break = trigger_close > float(previous["episode_high"])
            elif (
                previous["state"] == "balance"
                and current["state"] == "imbalance_down"
            ):
                boundary_break = trigger_close < float(previous["episode_low"])
            prior_timeline = timeline.loc[
                int(previous["_start_index"]) : int(previous["_end_index"])
            ]
            upper_tests = int(
                prior_timeline["mid_high"]
                .ge(float(previous["episode_high"]) - tolerance)
                .sum()
            )
            lower_tests = int(
                prior_timeline["mid_low"]
                .le(float(previous["episode_low"]) + tolerance)
                .sum()
            )
            prior_range = float(pre["window_range_pips"])
            trigger_range = float(trigger["window_range_pips"])
            activity_expansion = (
                trigger_range / prior_range if prior_range > 0 else np.nan
            )
            burst = bool(
                np.isfinite(activity_expansion)
                and activity_expansion >= config.transition.activity_burst_ratio
            )
            if boundary_break and burst:
                signature = "boundary_break_with_activity_burst"
            elif boundary_break:
                signature = "boundary_break"
            else:
                signature = "directional_repricing_inside_balance"
            transition_id = f"{segment_id}-tr{position - 1:05d}"
            rows.append(
                {
                    "sample_year": int(current["sample_year"]),
                    "segment_id": segment_id,
                    "transition_id": transition_id,
                    "from_episode_id": previous["episode_id"],
                    "to_episode_id": current["episode_id"],
                    "from_state": previous["state"],
                    "to_state": current["state"],
                    "transition_start": current["start_at"],
                    "confirmed_at": current["confirmed_at"],
                    "confirmation_latency_minutes": current[
                        "confirmation_latency_minutes"
                    ],
                    "prior_episode_duration_minutes": previous[
                        "duration_minutes"
                    ],
                    "prior_episode_width_pips": previous["episode_width_pips"],
                    "trigger_close": trigger_close,
                    "boundary_break": boundary_break,
                    "upper_boundary_tests": upper_tests,
                    "lower_boundary_tests": lower_tests,
                    "pre_transition_activity_ratio": pre["activity_ratio"],
                    "transition_activity_ratio": trigger["activity_ratio"],
                    "range_expansion_ratio": activity_expansion,
                    "activity_burst": burst,
                    "signature": signature,
                    "london_local_hour": int(trigger["london_local_hour"]),
                    "new_york_local_hour": int(trigger["new_york_local_hour"]),
                    "minutes_from_london_open": int(
                        trigger["minutes_from_london_open"]
                    ),
                    "minutes_from_new_york_open": int(
                        trigger["minutes_from_new_york_open"]
                    ),
                    "clock_bucket": trigger["clock_bucket"],
                    "opening_catalyst_window": bool(
                        (
                            0
                            <= int(trigger["minutes_from_london_open"])
                            < config.transition.opening_window_minutes
                        )
                        or (
                            0
                            <= int(trigger["minutes_from_new_york_open"])
                            < config.transition.opening_window_minutes
                        )
                    ),
                }
            )
    return pd.DataFrame(rows)
