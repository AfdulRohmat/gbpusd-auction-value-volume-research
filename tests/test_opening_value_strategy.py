from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from gbpusd_research.config import load_opening_value_strategy_config
from gbpusd_research.research.opening_value_strategy import (
    simulate_opening_value_strategy,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIP_SIZE = 0.0001


def _config():
    return load_opening_value_strategy_config(
        PROJECT_ROOT / "config/opening_value_strategy.yaml"
    )


def _event(*, state: str, opened: str = "2025-01-06 13:00:00Z") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_id": [f"event-{state}"],
            "session_name": ["new_york"],
            "local_session_date": [date(2025, 1, 6)],
            "event_timestamp_utc": [pd.Timestamp(opened)],
            "fx_trading_day": [date(2025, 1, 6)],
            "eligible": [True],
            "value_eligible": [True],
            "value_state": [state],
            "previous_profile_day": [date(2025, 1, 3)],
            "previous_poc": [1.0950 if state == "above_value" else 1.1050],
            "previous_vah": [1.1000],
            "previous_val": [1.1000],
        }
    )


def _bars(*, opened: str = "2025-01-06 13:00:00Z") -> pd.DataFrame:
    timestamps = pd.date_range(opened, periods=18, freq="5min")
    mid_open = np.full(18, 1.1000)
    mid_high = np.full(18, 1.1005)
    mid_low = np.full(18, 1.0995)
    mid_close = np.full(18, 1.1000)
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


def test_short_reentry_uses_next_bid_open_and_previous_poc_target() -> None:
    bars = _bars()
    bars.loc[0, ["mid_high", "ask_high"]] = [1.1025, 1.1026]
    bars.loc[0, "mid_close"] = 1.0998
    bars.loc[0, "bid_close"] = 1.0997
    bars.loc[0, "ask_close"] = 1.0999
    bars.loc[2, ["ask_low", "bid_low"]] = [1.0949, 1.0947]

    result = simulate_opening_value_strategy(
        _event(state="above_value"),
        bars,
        _config(),
        pip_size=PIP_SIZE,
        sample_role="validation",
    ).iloc[0]

    assert result["trade_executed"]
    assert result["direction"] == -1
    assert result["signal_timestamp_utc"] == pd.Timestamp("2025-01-06 13:00Z")
    assert result["entry_timestamp_utc"] == pd.Timestamp("2025-01-06 13:05Z")
    assert np.isclose(result["entry_price"], bars.loc[1, "bid_open"] - 0.00001)
    assert np.isclose(result["stop_price"], 1.1026)
    assert result["exit_reason"] == "target"
    assert np.isclose(result["exit_price"], 1.09501)


def test_ambiguous_long_bar_is_stopped_first_with_bid_execution() -> None:
    bars = _bars()
    bars.loc[0, ["mid_low", "bid_low"]] = [1.0970, 1.0969]
    bars.loc[0, "mid_close"] = 1.1001
    bars.loc[0, "bid_close"] = 1.1000
    bars.loc[0, "ask_close"] = 1.1002
    bars.loc[1, "bid_low"] = 1.0960
    bars.loc[1, "bid_high"] = 1.1060

    result = simulate_opening_value_strategy(
        _event(state="below_value"),
        bars,
        _config(),
        pip_size=PIP_SIZE,
        sample_role="validation",
    ).iloc[0]

    assert result["exit_reason"] == "stop"
    assert result["ambiguous_bar_stop_first"]
    assert np.isclose(result["stop_price"], 1.0969)
    assert np.isclose(result["exit_price"], 1.09689)
    assert result["r_multiple"] < 0


def test_future_bar_sentinel_cannot_change_signal_entry_or_stop() -> None:
    bars = _bars()
    bars.loc[0, ["mid_high", "ask_high"]] = [1.1025, 1.1026]
    bars.loc[0, "mid_close"] = 1.0998
    bars.loc[2, "ask_low"] = 1.0949
    baseline = simulate_opening_value_strategy(
        _event(state="above_value"),
        bars,
        _config(),
        pip_size=PIP_SIZE,
        sample_role="validation",
    ).iloc[0]
    bars.loc[17, ["mid_high", "ask_high"]] = [9.0, 9.1]
    sentinel = simulate_opening_value_strategy(
        _event(state="above_value"),
        bars,
        _config(),
        pip_size=PIP_SIZE,
        sample_role="validation",
    ).iloc[0]

    for column in (
        "signal_timestamp_utc",
        "entry_timestamp_utc",
        "entry_price",
        "stop_price",
    ):
        assert sentinel[column] == baseline[column]


def test_missing_m5_bar_excludes_complete_opening_window() -> None:
    bars = _bars().drop(index=8)
    result = simulate_opening_value_strategy(
        _event(state="above_value"),
        bars,
        _config(),
        pip_size=PIP_SIZE,
        sample_role="validation",
    ).iloc[0]

    assert not result["trade_executed"]
    assert result["strategy_exclusion_reason"] == "incomplete_opening_window"


def test_unfavorable_poc_excludes_trade_after_signal() -> None:
    events = _event(state="above_value")
    events.loc[0, "previous_poc"] = 1.1010
    bars = _bars()
    bars.loc[0, "mid_close"] = 1.0998
    result = simulate_opening_value_strategy(
        events,
        bars,
        _config(),
        pip_size=PIP_SIZE,
        sample_role="validation",
    ).iloc[0]

    assert result["signal_found"]
    assert not result["trade_executed"]
    assert result["strategy_exclusion_reason"] == "poc_target_not_favorable"
