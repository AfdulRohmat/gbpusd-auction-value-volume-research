from datetime import date

import pandas as pd
import pytest

from gbpusd_research.features.volume_profile import (
    attach_previous_profile,
    calculate_value_area,
)


def test_value_area_has_deterministic_poc_and_contiguous_expansion() -> None:
    profile = calculate_value_area(
        {10000: 4, 10001: 4, 10002: 1},
        bin_size_price=0.0001,
        value_area_fraction=0.70,
    )

    assert profile["poc"] == pytest.approx(1.0001)
    assert profile["val"] == pytest.approx(1.0000)
    assert profile["vah"] == pytest.approx(1.0001)
    assert profile["value_area_activity_fraction"] == pytest.approx(8 / 9)


def test_event_uses_latest_eligible_completed_profile() -> None:
    events = pd.DataFrame(
        {
            "fx_trading_day": [date(2024, 1, 8)],
            "open_price_mid": [1.1020],
        }
    )
    profiles = pd.DataFrame(
        {
            "profile_day": [
                date(2024, 1, 4),
                date(2024, 1, 5),
                date(2024, 1, 8),
            ],
            "eligible": [True, True, True],
            "poc": [1.0900, 1.1000, 1.2000],
            "val": [1.0800, 1.0950, 1.1900],
            "vah": [1.0950, 1.1010, 1.2100],
            "value_width_pips": [150.0, 60.0, 200.0],
            "total_activity": [100, 200, 300],
            "m5_coverage_ratio": [1.0, 1.0, 1.0],
        }
    )

    result = attach_previous_profile(
        events, profiles, pip_size=0.0001, boundary_buffer_pips=1.0
    ).iloc[0]

    assert result["previous_profile_day"] == date(2024, 1, 5)
    assert result["previous_poc"] == pytest.approx(1.1000)
    assert result["value_state"] == "above_value"


def test_ineligible_profile_is_skipped() -> None:
    events = pd.DataFrame(
        {"fx_trading_day": [date(2024, 1, 8)], "open_price_mid": [1.1000]}
    )
    profiles = pd.DataFrame(
        {
            "profile_day": [date(2024, 1, 5)],
            "eligible": [False],
            "poc": [1.1000],
            "val": [1.0950],
            "vah": [1.1050],
            "value_width_pips": [100.0],
            "total_activity": [100],
            "m5_coverage_ratio": [0.5],
        }
    )

    result = attach_previous_profile(
        events, profiles, pip_size=0.0001, boundary_buffer_pips=1.0
    ).iloc[0]

    assert not result["profile_available"]
