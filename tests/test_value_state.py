import numpy as np
import pandas as pd

from gbpusd_research.research.value_state import attach_value_outcomes


def test_outcomes_label_acceptance_and_reentry_without_changing_state() -> None:
    events = pd.DataFrame(
        {
            "event_timestamp_utc": pd.to_datetime(["2024-01-08 10:00:00Z"]),
            "value_state": ["above_value"],
            "previous_vah": [1.1000],
            "previous_val": [1.0900],
            "profile_available": [True],
            "eligible": [True],
            "vwap_available": [True],
            "vwap_slope_pips": [0.5],
            "fwd_15_return_pips": [5.0],
            "fwd_15_up_excursion_pips": [8.0],
            "fwd_15_down_excursion_pips": [3.0],
        }
    )
    bars = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2024-01-08 10:00:00Z",
                    "2024-01-08 10:05:00Z",
                    "2024-01-08 10:10:00Z",
                    "2024-01-08 10:15:00Z",
                ]
            ),
            "mid_close": [1.1020, 1.1021, 1.1000, 1.2000],
        }
    )

    result = attach_value_outcomes(
        events,
        bars,
        pip_size=0.0001,
        boundary_buffer_pips=1.0,
        acceptance_consecutive_closes=2,
        horizons=(15,),
    ).iloc[0]

    assert result["value_state"] == "above_value"
    assert result["value_fwd_15_acceptance_above"]
    assert result["value_fwd_15_reentered"]
    assert result["value_fwd_15_state_aligned_return_pips"] == 5.0
    assert result["value_eligible"]
    assert result["value_exclusion_reason"] is None


def test_inside_value_has_no_directional_outcome() -> None:
    events = pd.DataFrame(
        {
            "event_timestamp_utc": pd.to_datetime(["2024-01-08 10:00:00Z"]),
            "value_state": ["inside_value"],
            "previous_vah": [1.1000],
            "previous_val": [1.0900],
            "profile_available": [True],
            "eligible": [True],
            "vwap_available": [True],
            "vwap_slope_pips": [0.5],
            "fwd_15_return_pips": [5.0],
            "fwd_15_up_excursion_pips": [8.0],
            "fwd_15_down_excursion_pips": [3.0],
        }
    )
    bars = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2024-01-08 10:00:00Z"]),
            "mid_close": [1.0950],
        }
    )

    result = attach_value_outcomes(
        events,
        bars,
        pip_size=0.0001,
        boundary_buffer_pips=1.0,
        acceptance_consecutive_closes=2,
        horizons=(15,),
    ).iloc[0]

    assert np.isnan(result["value_fwd_15_state_aligned_return_pips"])
    assert np.isnan(result["value_fwd_15_reentered"])
