from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from gbpusd_research.config import load_project_config
from gbpusd_research.features.sessions import (
    build_session_calendar,
    fx_trading_day,
    session_coverage,
    tag_session_windows,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = load_project_config(
    PROJECT_ROOT / "config/research.yaml",
    PROJECT_ROOT / "config/sessions.yaml",
)


def calendar_for_day(day: date) -> pd.DataFrame:
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    return build_session_calendar(
        start,
        start + timedelta(days=1),
        CONFIG.sessions,
    )


@pytest.mark.parametrize(
    ("day", "london_hour", "new_york_hour"),
    [
        (date(2024, 1, 2), 8, 13),
        (date(2024, 7, 2), 7, 12),
        # US DST starts before UK DST.
        (date(2024, 3, 11), 8, 12),
        # UK standard time resumes before US standard time.
        (date(2024, 10, 28), 8, 12),
        (date(2024, 11, 4), 8, 13),
    ],
)
def test_session_opens_follow_independent_dst(
    day: date, london_hour: int, new_york_hour: int
) -> None:
    calendar = calendar_for_day(day).set_index("session_name")

    assert calendar.loc["london", "open_timestamp_utc"].hour == london_hour
    assert calendar.loc["new_york", "open_timestamp_utc"].hour == new_york_hour


def test_fx_trading_day_rolls_at_new_york_17() -> None:
    trading_day = CONFIG.sessions.trading_day

    assert fx_trading_day(pd.Timestamp("2024-01-07 21:59:59Z"), trading_day) == date(
        2024, 1, 7
    )
    assert fx_trading_day(pd.Timestamp("2024-01-07 22:00:00Z"), trading_day) == date(
        2024, 1, 8
    )


def test_fx_trading_day_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        fx_trading_day(datetime(2024, 1, 2), CONFIG.sessions.trading_day)


def test_tagging_uses_half_open_90_minute_window() -> None:
    calendar = calendar_for_day(date(2024, 1, 2))
    bars = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2024-01-02 07:55Z",
                    "2024-01-02 08:00Z",
                    "2024-01-02 09:25Z",
                    "2024-01-02 09:30Z",
                    "2024-01-02 13:00Z",
                ]
            )
        }
    )

    tagged = tag_session_windows(bars, calendar)

    assert tagged["session_name"].tolist() == [
        pd.NA,
        "london",
        "london",
        pd.NA,
        "new_york",
    ]
    assert tagged.loc[1, "minutes_from_session_open"] == 0
    assert tagged.loc[2, "minutes_from_session_open"] == 85


def test_complete_windows_have_18_bars_each() -> None:
    calendar = calendar_for_day(date(2024, 1, 2))
    bars = pd.DataFrame(
        {"timestamp": pd.date_range("2024-01-02 00:00Z", periods=288, freq="5min")}
    )

    coverage = session_coverage(bars, calendar, minimum_ratio=0.95)

    assert coverage["expected_bars"].tolist() == [18, 18]
    assert coverage["observed_bars"].tolist() == [18, 18]
    assert coverage["eligible"].all()


def test_overlapping_session_definitions_are_rejected() -> None:
    calendar = calendar_for_day(date(2024, 1, 2))
    duplicate = calendar.iloc[[0]].copy()
    duplicate["session_name"] = "collision"
    overlapping = pd.concat([calendar, duplicate], ignore_index=True)
    bars = pd.DataFrame({"timestamp": pd.to_datetime(["2024-01-02 08:00Z"])})

    with pytest.raises(ValueError, match="Overlapping"):
        tag_session_windows(bars, overlapping)
