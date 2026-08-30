from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from gbpusd_research.config import (
    load_opening_ablation_config,
    load_opening_value_strategy_config,
)
from gbpusd_research.research.opening_ablation import simulate_opening_ablation

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIP_SIZE = 0.0001


def _configs():
    strategy = load_opening_value_strategy_config(
        PROJECT_ROOT / "config/opening_value_strategy.yaml"
    )
    ablation = load_opening_ablation_config(
        PROJECT_ROOT / "config/opening_ablation.yaml"
    )
    return strategy, ablation


def _event(*, poc: float = 1.0950) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_id": ["event-above"],
            "session_name": ["new_york"],
            "local_session_date": [date(2025, 1, 6)],
            "event_timestamp_utc": [pd.Timestamp("2025-01-06 13:00Z")],
            "fx_trading_day": [date(2025, 1, 6)],
            "eligible": [True],
            "value_eligible": [True],
            "value_state": ["above_value"],
            "previous_profile_day": [date(2025, 1, 3)],
            "previous_poc": [poc],
            "previous_vah": [1.1000],
            "previous_val": [1.0900],
        }
    )


def _bars(*, signal: bool = True) -> pd.DataFrame:
    timestamps = pd.date_range("2025-01-06 13:00Z", periods=18, freq="5min")
    mid_open = np.full(18, 1.1020)
    mid_close = np.full(18, 1.1010)
    mid_high = np.full(18, 1.1025)
    mid_low = np.full(18, 1.1005)
    if signal:
        mid_close[0] = 1.0998
        mid_low[0] = 1.0995
        mid_open[1:] = 1.0998
        mid_close[1:] = 1.0995
        mid_high[1:] = 1.1002
        mid_low[1:] = 1.0990
        mid_low[2] = 1.0948
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


def test_ablation_generates_every_frozen_variant_for_favorable_signal() -> None:
    strategy, ablation = _configs()
    result = simulate_opening_ablation(
        _event(),
        _bars(),
        strategy,
        ablation,
        pip_size=PIP_SIZE,
        sample_year=2025,
    )

    assert set(result["variant"]) == set(ablation.analysis.variants)
    assert result[["event_id", "variant"]].duplicated().sum() == 0
    open_row = result[result["variant"].eq("open_timeout_90")].iloc[0]
    confirmed = result[result["variant"].eq("confirmed_timeout_all")].iloc[0]
    assert open_row["entry_timestamp_utc"] == pd.Timestamp("2025-01-06 13:00Z")
    assert confirmed["entry_timestamp_utc"] == pd.Timestamp(
        "2025-01-06 13:05Z"
    )
    assert np.isclose(open_row["entry_price"], 1.10189)
    assert np.isclose(confirmed["entry_price"], 1.09969)
    assert not result[result["variant"].eq("phase4_full")].iloc[0][
        "diagnostic_only"
    ]


def test_no_reentry_retains_only_open_time_variants() -> None:
    strategy, ablation = _configs()
    result = simulate_opening_ablation(
        _event(),
        _bars(signal=False),
        strategy,
        ablation,
        pip_size=PIP_SIZE,
        sample_year=2025,
    )

    assert set(result["variant"]) == {
        "open_timeout_30",
        "open_timeout_60",
        "open_timeout_90",
        "open_boundary_90",
        "open_poc_90",
    }


def test_unfavorable_poc_retains_signal_but_excludes_poc_cohort() -> None:
    strategy, ablation = _configs()
    result = simulate_opening_ablation(
        _event(poc=1.1010),
        _bars(),
        strategy,
        ablation,
        pip_size=PIP_SIZE,
        sample_year=2025,
    )

    assert "signal_cohort_open_timeout_90" in set(result["variant"])
    assert "confirmed_timeout_all" in set(result["variant"])
    assert "confirmed_timeout_favorable" not in set(result["variant"])
    assert "confirmed_poc_no_stop" not in set(result["variant"])
    assert "phase4_full" not in set(result["variant"])


def test_open_boundary_target_uses_short_ask_and_exit_slippage() -> None:
    strategy, ablation = _configs()
    result = simulate_opening_ablation(
        _event(),
        _bars(),
        strategy,
        ablation,
        pip_size=PIP_SIZE,
        sample_year=2025,
    )
    boundary = result[result["variant"].eq("open_boundary_90")].iloc[0]

    assert boundary["exit_reason"] == "target"
    assert np.isclose(boundary["exit_price"], 1.10001)
    assert boundary["exit_timestamp_utc"] == pd.Timestamp("2025-01-06 13:05Z")
