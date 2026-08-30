from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from gbpusd_research.config import (
    load_balance_boundary_strategy_config,
    load_project_config,
)
from gbpusd_research.research.balance_boundary_strategy import (
    build_analysis_trades,
    build_opening_balance_context,
    simulate_balance_boundary,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIP_SIZE = 0.0001


def _config():
    return load_balance_boundary_strategy_config(
        PROJECT_ROOT / "config/balance_boundary_strategy.yaml"
    )


def _sessions():
    return load_project_config(
        PROJECT_ROOT / "config/research_2025.yaml",
        PROJECT_ROOT / "config/sessions.yaml",
    ).sessions


def _calendar() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "session_name": ["new_york"],
            "local_session_date": [date(2025, 1, 6)],
            "open_timestamp_utc": [pd.Timestamp("2025-01-06 13:00Z")],
            "fx_trading_day": [date(2025, 1, 6)],
        }
    )


def _bars() -> pd.DataFrame:
    timestamps = pd.date_range("2025-01-06 13:00Z", periods=108, freq="5min")
    mid_open = np.full(108, 1.1005)
    mid_high = np.full(108, 1.1007)
    mid_low = np.full(108, 1.1003)
    mid_close = np.full(108, 1.1005)
    spread = 0.0001
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "mid_open": mid_open,
            "mid_high": mid_high,
            "mid_low": mid_low,
            "mid_close": mid_close,
            "bid_open": mid_open - spread / 2,
            "bid_high": mid_high - spread / 2,
            "bid_low": mid_low - spread / 2,
            "bid_close": mid_close - spread / 2,
            "ask_open": mid_open + spread / 2,
            "ask_high": mid_high + spread / 2,
            "ask_low": mid_low + spread / 2,
            "ask_close": mid_close + spread / 2,
        }
    )


def _timeline(bars: pd.DataFrame) -> pd.DataFrame:
    pre_timestamps = pd.date_range(
        "2025-01-06 12:30Z", periods=6, freq="5min"
    )
    session_timestamps = pd.to_datetime(bars["timestamp"], utc=True)
    timestamps = pre_timestamps.append(pd.DatetimeIndex(session_timestamps))
    size = len(timestamps)
    mid_high = np.full(size, 1.1007)
    mid_low = np.full(size, 1.1003)
    mid_high[:6] = [1.1008, 1.1009, 1.1010, 1.1009, 1.1008, 1.1009]
    mid_low[:6] = [1.1002, 1.1001, 1.1000, 1.1001, 1.1002, 1.1001]
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "available_at": timestamps + np.timedelta64(5, "m"),
            "raw_auction_state": ["balance"] * size,
            "observable_state": ["balance"] * size,
            "observable_episode_id": ["bal-1"] * size,
            "episode_id": ["bal-1"] * size,
            "activity_regime": ["normal"] * size,
            "activity_ratio": np.ones(size),
            "mid_high": mid_high,
            "mid_low": mid_low,
        }
    )


def _episodes() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "episode_id": ["bal-1"],
            "state": ["balance"],
            "start_at": [pd.Timestamp("2025-01-06 12:30Z")],
            "confirmed_at": [pd.Timestamp("2025-01-06 12:35Z")],
        }
    )


def _empty_transitions() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "from_state",
            "to_state",
            "from_episode_id",
            "to_episode_id",
            "confirmed_at",
        ]
    )


def test_boundary_is_frozen_using_only_rows_available_at_open() -> None:
    bars = _bars()
    timeline = _timeline(bars)
    baseline = build_opening_balance_context(
        _calendar(),
        timeline,
        _episodes(),
        pip_size=PIP_SIZE,
        sample_year=2025,
    ).iloc[0]
    future = timeline["timestamp"].eq(pd.Timestamp("2025-01-06 13:30Z"))
    timeline.loc[future, "mid_high"] = 9.0
    sentinel = build_opening_balance_context(
        _calendar(),
        timeline,
        _episodes(),
        pip_size=PIP_SIZE,
        sample_year=2025,
    ).iloc[0]

    assert baseline["balance_high"] == sentinel["balance_high"] == 1.1010
    assert baseline["balance_low"] == sentinel["balance_low"] == 1.1000
    assert baseline["boundary_available_at_max"] <= baseline["event_timestamp_utc"]


def test_boundary_does_not_use_future_episode_assignment() -> None:
    bars = _bars()
    timeline = _timeline(bars)
    opening_row = timeline["timestamp"].eq(pd.Timestamp("2025-01-06 12:55Z"))
    timeline.loc[opening_row, "episode_id"] = "future-up-1"
    timeline.loc[opening_row, "mid_high"] = 1.1020

    context = build_opening_balance_context(
        _calendar(),
        timeline,
        _episodes(),
        pip_size=PIP_SIZE,
        sample_year=2025,
    ).iloc[0]

    assert context["observable_episode_id"] == "bal-1"
    assert context["balance_high"] == 1.1020


def test_lower_rejection_enters_next_bar_and_targets_midpoint() -> None:
    bars = _bars()
    bars.loc[0, ["mid_low", "mid_close"]] = [1.1000, 1.1002]
    bars.loc[1, "mid_open"] = 1.10002
    bars.loc[1, "bid_open"] = 1.09997
    bars.loc[1, "ask_open"] = 1.10007
    timeline = _timeline(bars)
    timeline.loc[timeline["timestamp"].eq(bars.loc[0, "timestamp"]), "mid_low"] = 1.1000
    events, trades = simulate_balance_boundary(
        _calendar(),
        bars,
        timeline,
        _episodes(),
        _empty_transitions(),
        _config(),
        _sessions(),
        pip_size=PIP_SIZE,
        sample_year=2025,
    )

    event = events.iloc[0]
    trade = trades.iloc[0]
    assert event["trigger_setup"] == "rejection"
    assert trade["variant"] == "rotation_midpoint"
    assert trade["direction"] == 1
    assert trade["entry_timestamp_utc"] == pd.Timestamp("2025-01-06 13:05Z")
    assert np.isclose(trade["target_fill_price"], 1.1005)
    assert trade["nominal_reward_r"] >= 1.5
    assert trade["exit_reason"] == "midpoint_target"


def test_raw_imbalance_candidate_is_not_faded() -> None:
    bars = _bars()
    bars.loc[0, ["mid_low", "mid_close"]] = [1.1000, 1.1002]
    timeline = _timeline(bars)
    mask = timeline["timestamp"].eq(bars.loc[0, "timestamp"])
    timeline.loc[mask, "raw_auction_state"] = "imbalance_up"
    events, trades = simulate_balance_boundary(
        _calendar(),
        bars,
        timeline,
        _episodes(),
        _empty_transitions(),
        _config(),
        _sessions(),
        pip_size=PIP_SIZE,
        sample_year=2025,
    )

    assert not events.iloc[0]["trade_executed"]
    assert events.iloc[0]["strategy_exclusion_reason"] == "no_valid_boundary_trigger"
    assert trades.empty


def test_acceptance_requires_confirmed_state_and_two_outside_closes() -> None:
    bars = _bars()
    bars.loc[0, ["mid_open", "mid_high", "mid_low", "mid_close"]] = [
        1.1009,
        1.1012,
        1.1008,
        1.1011,
    ]
    bars.loc[1, ["mid_open", "mid_high", "mid_low", "mid_close"]] = [
        1.1011,
        1.1013,
        1.1010,
        1.1012,
    ]
    for column in ("open", "high", "low", "close"):
        bars.loc[:1, f"bid_{column}"] = bars.loc[:1, f"mid_{column}"] - 0.00005
        bars.loc[:1, f"ask_{column}"] = bars.loc[:1, f"mid_{column}"] + 0.00005
    bars.loc[2, ["mid_open", "bid_open", "ask_open"]] = [
        1.1012,
        1.10115,
        1.10125,
    ]
    bars.loc[2, ["mid_high", "bid_high", "ask_high"]] = [
        1.1022,
        1.10215,
        1.10225,
    ]
    bars.loc[2, ["mid_low", "bid_low", "ask_low"]] = [
        1.1011,
        1.10105,
        1.10115,
    ]
    timeline = _timeline(bars)
    first = timeline["timestamp"].eq(bars.loc[0, "timestamp"])
    second = timeline["timestamp"].eq(bars.loc[1, "timestamp"])
    timeline.loc[first, "raw_auction_state"] = "imbalance_up"
    timeline.loc[second, "raw_auction_state"] = "imbalance_up"
    timeline.loc[second, "observable_state"] = "imbalance_up"
    timeline.loc[second, "observable_episode_id"] = "up-1"
    timeline.loc[second, "episode_id"] = "up-1"
    transitions = pd.DataFrame(
        {
            "from_state": ["balance"],
            "to_state": ["imbalance_up"],
            "from_episode_id": ["bal-1"],
            "to_episode_id": ["up-1"],
            "confirmed_at": [pd.Timestamp("2025-01-06 13:10Z")],
        }
    )
    events, trades = simulate_balance_boundary(
        _calendar(),
        bars,
        timeline,
        _episodes(),
        transitions,
        _config(),
        _sessions(),
        pip_size=PIP_SIZE,
        sample_year=2025,
    )

    assert events.iloc[0]["trigger_setup"] == "acceptance"
    assert set(trades["variant"]) == {
        "acceptance_fixed_2r",
        "acceptance_trailing_session",
    }
    assert trades["entry_timestamp_utc"].eq(pd.Timestamp("2025-01-06 13:10Z")).all()
    fixed = trades[trades["variant"].eq("acceptance_fixed_2r")].iloc[0]
    assert fixed["exit_reason"] == "target_2r"
    assert np.isclose(fixed["r_multiple"], 2.0)


def test_analysis_routes_reuse_the_registered_setup_execution() -> None:
    bars = _bars()
    bars.loc[0, ["mid_low", "mid_close"]] = [1.1000, 1.1002]
    bars.loc[1, ["mid_open", "bid_open", "ask_open"]] = [
        1.10002,
        1.09997,
        1.10007,
    ]
    _, trades = simulate_balance_boundary(
        _calendar(),
        bars,
        _timeline(bars),
        _episodes(),
        _empty_transitions(),
        _config(),
        _sessions(),
        pip_size=PIP_SIZE,
        sample_year=2025,
    )
    analysis = build_analysis_trades(trades, _config())

    assert set(analysis["analysis_variant"]) == {
        "rotation_midpoint",
        "combined_fixed_2r",
        "combined_trailing_session",
    }
    assert analysis["entry_price"].nunique() == 1
    assert analysis["r_multiple"].nunique() == 1
