from pathlib import Path

import numpy as np
import pandas as pd

from gbpusd_research.config import load_auction_taxonomy_config
from gbpusd_research.research.phase7 import (
    _wilson_interval,
    balance_hazard,
    opening_control_differences,
    opening_control_statistics,
    transition_matrix,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _config():
    return load_auction_taxonomy_config(
        PROJECT_ROOT / "config/auction_state_taxonomy.yaml"
    )


def _episodes() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "sample_year": [2025, 2025, 2025, 2025],
            "episode_id": ["b1", "u1", "b2", "d1"],
            "state": ["balance", "imbalance_up", "balance", "imbalance_down"],
            "duration_minutes": [45.0, 30.0, 150.0, 20.0],
            "right_censored": [False, False, False, True],
        }
    )


def _transitions() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "sample_year": [2025, 2025, 2025],
            "from_episode_id": ["b1", "u1", "b2"],
            "from_state": ["balance", "imbalance_up", "balance"],
            "to_state": ["imbalance_up", "balance", "imbalance_down"],
        }
    )


def test_transition_matrix_uses_outbound_state_denominator() -> None:
    matrix = transition_matrix(_episodes(), _transitions())
    balance = matrix[matrix["from_state"].eq("balance")]

    assert balance["transitions"].sum() == 2
    assert np.isclose(balance["conditional_probability"].sum(), 1.0)


def test_balance_hazard_uses_episode_age_at_risk() -> None:
    hazard = balance_hazard(_episodes(), _transitions(), _config())
    first = hazard.iloc[0]
    second = hazard.iloc[1]

    assert first["at_risk"] == 2
    assert first["balance_to_imbalance_transitions"] == 0
    assert first["exposure_minutes"] == 60.0
    assert second["at_risk"] == 2
    assert second["balance_to_imbalance_transitions"] == 1
    assert second["exposure_minutes"] == 45.0
    assert np.isclose(second["transition_rate_per_30m_exposure"], 2 / 3)


def test_opening_statistics_condition_on_balance_at_start() -> None:
    events = pd.DataFrame(
        {
            "sample_year": [2025, 2025, 2025],
            "session_name": ["london"] * 3,
            "event_kind": ["session_open"] * 3,
            "observable_state": ["balance", "balance", "imbalance_up"],
            "transition_within_15": [True, False, False],
            "transition_within_30": [True, False, False],
            "transition_within_60": [True, True, False],
            "transition_within_90": [True, True, False],
        }
    )
    statistics = opening_control_statistics(events, _config())
    sixty = statistics[statistics["horizon_minutes"].eq(60)].iloc[0]

    assert sixty["balance_at_start"] == 2
    assert sixty["balance_to_imbalance"] == 2
    assert sixty["transition_probability"] == 1.0


def test_wilson_interval_is_bounded() -> None:
    low, high = _wilson_interval(5, 10, 0.95)
    assert 0 < low < 0.5 < high < 1


def test_opening_control_difference_is_opening_minus_control() -> None:
    statistics = pd.DataFrame(
        {
            "sample_year": [2025, 2025, 2025],
            "session_name": ["london"] * 3,
            "horizon_minutes": [60] * 3,
            "event_kind": ["session_open", "fixed_control", "matched_control"],
            "transition_probability": [0.4, 0.3, 0.35],
        }
    )
    result = opening_control_differences(statistics).iloc[0]

    assert np.isclose(result["opening_minus_fixed"], 0.1)
    assert np.isclose(result["opening_minus_matched"], 0.05)
