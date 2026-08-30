from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from gbpusd_research.config import (
    load_opening_auction_config,
    load_project_config,
)
from gbpusd_research.research.opening_auction_state_machine import (
    _manage_variant,
    classify_opening_bars,
    simulate_opening_auction,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIP_SIZE = 0.0001


def _config():
    return load_opening_auction_config(
        PROJECT_ROOT / "config/opening_auction_state_machine.yaml"
    )


def _sessions():
    return load_project_config(
        PROJECT_ROOT / "config/research_2025.yaml",
        PROJECT_ROOT / "config/sessions.yaml",
    ).sessions


def _event() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_id": ["new-york-2025-01-06"],
            "session_name": ["new_york"],
            "local_session_date": [date(2025, 1, 6)],
            "event_timestamp_utc": [pd.Timestamp("2025-01-06 13:00Z")],
            "fx_trading_day": [date(2025, 1, 6)],
            "eligible": [True],
            "value_eligible": [False],
            "value_state": [None],
            "previous_profile_day": [None],
        }
    )


def _bars() -> pd.DataFrame:
    timestamps = pd.date_range("2025-01-06 13:00Z", periods=108, freq="5min")
    mid_open = np.full(108, 1.1009)
    mid_high = np.full(108, 1.1011)
    mid_low = np.full(108, 1.1007)
    mid_close = np.full(108, 1.1009)
    mid_open[:3] = [1.1000, 1.1003, 1.1006]
    mid_high[:3] = [1.1004, 1.1007, 1.1010]
    mid_low[:3] = [1.0999, 1.1002, 1.1005]
    mid_close[:3] = [1.1003, 1.1006, 1.1009]
    spread = 0.0002
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


def test_classifier_distinguishes_imbalance_and_balance() -> None:
    bars = _bars().iloc[:3].copy()
    imbalance = classify_opening_bars(bars, _config())
    assert imbalance["auction_state"] == "imbalance_up"
    assert imbalance["direction"] == 1
    assert np.isclose(imbalance["efficiency"], 1.0)

    bars.loc[:, "mid_close"] = [1.1008, 1.1002, 1.1006]
    bars.loc[:, "mid_high"] = [1.1010, 1.1009, 1.1008]
    bars.loc[:, "mid_low"] = [1.0998, 1.1000, 1.1001]
    balance = classify_opening_bars(bars, _config())
    assert balance["auction_state"] == "balance_high"
    assert balance["direction"] == -1
    assert balance["efficiency"] < 0.60


def test_simulator_uses_next_open_and_fixed_target_realizes_two_r() -> None:
    bars = _bars()
    # The fixed target is reached after entry without touching the initial stop.
    bars.loc[4, ["mid_high", "bid_high", "ask_high"]] = [1.1026, 1.1025, 1.1027]
    events, trades = simulate_opening_auction(
        _event(),
        bars,
        _config(),
        _sessions(),
        pip_size=PIP_SIZE,
        sample_year=2025,
    )

    event = events.iloc[0]
    fixed = trades[trades["variant"].eq("fixed_2r")].iloc[0]
    assert event["auction_state"] == "imbalance_up"
    assert event["trade_executed"]
    assert event["entry_timestamp_utc"] == pd.Timestamp("2025-01-06 13:15Z")
    assert np.isclose(event["entry_price"], bars.loc[3, "ask_open"] + 0.00001)
    assert fixed["exit_reason"] == "target_2r"
    assert np.isclose(fixed["r_multiple"], 2.0)
    assert len(trades) == 3


def test_future_bar_cannot_change_state_entry_or_initial_stop() -> None:
    bars = _bars()
    baseline_events, _ = simulate_opening_auction(
        _event(),
        bars,
        _config(),
        _sessions(),
        pip_size=PIP_SIZE,
        sample_year=2025,
    )
    bars.loc[50, ["mid_high", "bid_high", "ask_high"]] = [9.0, 8.9, 9.1]
    sentinel_events, _ = simulate_opening_auction(
        _event(),
        bars,
        _config(),
        _sessions(),
        pip_size=PIP_SIZE,
        sample_year=2025,
    )

    for column in (
        "auction_state",
        "direction",
        "efficiency",
        "entry_timestamp_utc",
        "entry_price",
        "initial_stop_trigger_price",
        "initial_risk_pips",
    ):
        assert sentinel_events.iloc[0][column] == baseline_events.iloc[0][column]


def test_incomplete_session_window_excludes_event() -> None:
    events, trades = simulate_opening_auction(
        _event(),
        _bars().drop(index=60),
        _config(),
        _sessions(),
        pip_size=PIP_SIZE,
        sample_year=2025,
    )

    assert events.iloc[0]["auction_exclusion_reason"] == "incomplete_session_window"
    assert trades.empty


def test_trailing_break_even_update_only_applies_to_next_bar() -> None:
    management = pd.DataFrame(
        {
            "timestamp": pd.date_range("2025-01-06 13:15Z", periods=2, freq="5min"),
            "bid_open": [1.1010, 1.1015],
            "bid_high": [1.1022, 1.1016],
            "bid_low": [1.1005, 1.1009],
            "bid_close": [1.1018, 1.1010],
            "ask_open": [1.1012, 1.1017],
            "ask_high": [1.1024, 1.1018],
            "ask_low": [1.1007, 1.1011],
            "ask_close": [1.1020, 1.1012],
        }
    )
    result = _manage_variant(
        management,
        variant="trailing_session",
        direction=1,
        entry_price=1.1010,
        initial_stop=1.1001,
        initial_risk_pips=10.0,
        config=_config(),
        pip_size=PIP_SIZE,
    )

    assert result["break_even_activated"]
    assert result["exit_bar_timestamp_utc"] == management.loc[1, "timestamp"]
    assert result["exit_reason"] == "trailing_stop"
    assert np.isclose(result["r_multiple"], 0.0)
