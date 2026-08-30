from pathlib import Path

import numpy as np
import pandas as pd

from gbpusd_research.config import load_auction_taxonomy_config
from gbpusd_research.research.auction_state_taxonomy import (
    build_state_episodes,
    build_state_timeline,
    build_state_transitions,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIP_SIZE = 0.0001


def _config():
    return load_auction_taxonomy_config(
        PROJECT_ROOT / "config/auction_state_taxonomy.yaml"
    )


def _bars(closes: list[float]) -> pd.DataFrame:
    values = np.asarray(closes, dtype=float)
    opens = np.concatenate(([values[0] - 0.0002], values[:-1]))
    high = np.maximum(opens, values) + 0.0001
    low = np.minimum(opens, values) - 0.0001
    return pd.DataFrame(
        {
            "timestamp": pd.date_range(
                "2025-01-06 00:00Z",
                periods=len(values),
                freq="5min",
            ),
            "mid_open": opens,
            "mid_high": high,
            "mid_low": low,
            "mid_close": values,
            "tick_count": np.full(len(values), 10),
            "spread_median_pips": np.full(len(values), 0.7),
        }
    )


def test_rolling_features_label_directional_imbalance() -> None:
    bars = _bars([1.1002, 1.1004, 1.1006, 1.1008, 1.1010, 1.1012])
    timeline = build_state_timeline(
        bars,
        _config(),
        pip_size=PIP_SIZE,
        sample_year=2025,
    )
    final = timeline.iloc[-1]

    assert final["raw_auction_state"] == "imbalance_up"
    assert np.isclose(final["efficiency"], 1.0)
    assert np.isclose(final["directional_persistence"], 1.0)
    assert final["available_at"] == pd.Timestamp("2025-01-06 00:30Z")


def test_rolling_features_label_rotational_balance() -> None:
    bars = _bars([1.1002, 1.1000, 1.1002, 1.1000, 1.1002, 1.1000])
    # Make adjacent bars overlap extensively around the same midpoint.
    bars["mid_high"] = 1.10035
    bars["mid_low"] = 1.09985
    timeline = build_state_timeline(
        bars,
        _config(),
        pip_size=PIP_SIZE,
        sample_year=2025,
    )

    assert timeline.iloc[-1]["raw_auction_state"] == "balance"
    assert timeline.iloc[-1]["midpoint_crossings"] >= 1
    assert timeline.iloc[-1]["mean_overlap"] == 1.0


def test_rolling_features_restart_after_timestamp_gap() -> None:
    first = _bars([1.1002, 1.1004, 1.1006, 1.1008, 1.1010, 1.1012])
    second = _bars([1.1014, 1.1016, 1.1018, 1.1020, 1.1022, 1.1024])
    second["timestamp"] += np.timedelta64(2, "D")
    bars = pd.concat([first, second], ignore_index=True)

    timeline = build_state_timeline(
        bars,
        _config(),
        pip_size=PIP_SIZE,
        sample_year=2025,
    )

    assert timeline.loc[5, "raw_auction_state"] == "imbalance_up"
    assert timeline.loc[6:10, "raw_auction_state"].eq("warmup").all()
    assert timeline.loc[11, "raw_auction_state"] == "imbalance_up"
    assert timeline.loc[5, "segment_id"] != timeline.loc[6, "segment_id"]


def _manual_timeline() -> pd.DataFrame:
    states = [
        "transition",
        "balance",
        "balance",
        "transition",
        "imbalance_up",
        "imbalance_up",
        "imbalance_up",
        "balance",
        "balance",
    ]
    timestamps = pd.date_range("2025-01-06 00:00Z", periods=len(states), freq="5min")
    values = np.linspace(1.1000, 1.1008, len(states))
    return pd.DataFrame(
        {
            "sample_year": 2025,
            "segment_id": "2025-0",
            "segment_number": 0,
            "timestamp": timestamps,
            "available_at": timestamps + np.timedelta64(5, "m"),
            "mid_open": values,
            "mid_high": values + 0.0002,
            "mid_low": values - 0.0002,
            "mid_close": values,
            "tick_count": 10,
            "spread_median_pips": 0.7,
            "window_high": values + 0.0003,
            "window_low": values - 0.0003,
            "window_range_pips": 6.0,
            "displacement_pips": 1.0,
            "path_pips": 2.0,
            "efficiency": 0.5,
            "mean_overlap": 0.5,
            "directional_persistence": 0.5,
            "midpoint_crossings": 1,
            "close_location": 0.5,
            "raw_auction_state": states,
            "activity_baseline_range_pips": 6.0,
            "activity_ratio": 1.0,
            "activity_regime": "normal",
            "london_local_hour": 0,
            "new_york_local_hour": 19,
            "minutes_from_london_open": -480,
            "minutes_from_new_york_open": 660,
            "clock_bucket": "other",
        }
    )


def test_confirmation_preserves_live_state_until_second_window() -> None:
    timeline, episodes = build_state_episodes(
        _manual_timeline(),
        _config(),
        pip_size=PIP_SIZE,
    )

    assert episodes["state"].tolist() == ["balance", "imbalance_up", "balance"]
    assert episodes["confirmation_latency_minutes"].eq(5).all()
    assert pd.isna(timeline.loc[1, "observable_state"])
    assert timeline.loc[2, "observable_state"] == "balance"
    assert timeline.loc[4, "observable_state"] == "balance"
    assert timeline.loc[5, "observable_state"] == "imbalance_up"


def test_transitions_connect_adjacent_confirmed_episodes() -> None:
    timeline, episodes = build_state_episodes(
        _manual_timeline(),
        _config(),
        pip_size=PIP_SIZE,
    )
    transitions = build_state_transitions(
        timeline,
        episodes,
        _config(),
        pip_size=PIP_SIZE,
    )

    assert len(transitions) == 2
    first = transitions.iloc[0]
    assert first["from_state"] == "balance"
    assert first["to_state"] == "imbalance_up"
    assert first["confirmed_at"] > first["transition_start"]


def test_future_bar_cannot_change_earlier_state_or_activity_baseline() -> None:
    config = _config().model_copy(
        update={
            "activity": _config().activity.model_copy(
                update={"baseline_bars": 12, "minimum_baseline_bars": 6}
            )
        }
    )
    closes = [1.1000 + (index % 3) * 0.0001 for index in range(30)]
    bars = _bars(closes)
    baseline = build_state_timeline(
        bars,
        config,
        pip_size=PIP_SIZE,
        sample_year=2025,
    )
    bars.loc[29, ["mid_high", "mid_low", "mid_close"]] = [9.0, 0.1, 8.0]
    sentinel = build_state_timeline(
        bars,
        config,
        pip_size=PIP_SIZE,
        sample_year=2025,
    )

    for column in (
        "raw_auction_state",
        "efficiency",
        "activity_baseline_range_pips",
        "activity_ratio",
    ):
        assert sentinel.loc[20, column] == baseline.loc[20, column]
