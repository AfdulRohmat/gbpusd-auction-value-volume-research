from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from gbpusd_research.config import load_project_config
from gbpusd_research.features.sessions import build_session_calendar
from gbpusd_research.research.controls import (
    build_fixed_control_calendar,
    build_matched_control_calendar,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = load_project_config(
    PROJECT_ROOT / "config/research.yaml",
    PROJECT_ROOT / "config/sessions.yaml",
)


def test_fixed_controls_use_registered_local_times() -> None:
    start = datetime(2024, 1, 2, tzinfo=UTC)
    calendar = build_session_calendar(start, start + timedelta(days=1), CONFIG.sessions)

    controls = build_fixed_control_calendar(calendar, CONFIG.sessions).set_index(
        "session_name"
    )

    assert controls.loc["london", "open_timestamp_utc"] == pd.Timestamp(
        "2024-01-02 04:00Z"
    )
    assert controls.loc["new_york", "open_timestamp_utc"] == pd.Timestamp(
        "2024-01-02 17:00Z"
    )


def test_matched_controls_are_deterministic_and_avoid_openings() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=31)
    calendar = build_session_calendar(start, end, CONFIG.sessions)
    bars = pd.DataFrame(
        {"timestamp": pd.date_range(start, end, freq="5min", inclusive="left")}
    )

    first = build_matched_control_calendar(
        bars,
        calendar,
        CONFIG.sessions,
        preopen_minutes=90,
        forward_minutes=90,
        random_seed=42,
    )
    second = build_matched_control_calendar(
        bars,
        calendar,
        CONFIG.sessions,
        preopen_minutes=90,
        forward_minutes=90,
        random_seed=42,
    )

    assert first["open_timestamp_utc"].tolist() == second["open_timestamp_utc"].tolist()
    for selected in first["open_timestamp_utc"]:
        for opened in calendar["open_timestamp_utc"]:
            assert not (
                opened - timedelta(minutes=210)
                <= selected
                <= opened + timedelta(minutes=210)
            )
