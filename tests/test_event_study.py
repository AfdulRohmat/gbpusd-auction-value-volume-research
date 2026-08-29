from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gbpusd_research.config import load_project_config
from gbpusd_research.features.sessions import build_session_calendar
from gbpusd_research.research.event_study import build_event_dataset

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = load_project_config(
    PROJECT_ROOT / "config/research.yaml",
    PROJECT_ROOT / "config/sessions.yaml",
)


def synthetic_bars() -> pd.DataFrame:
    timestamps = pd.date_range("2024-01-02 07:00Z", periods=31, freq="5min")
    opened = 1.2000 + np.arange(len(timestamps)) * 0.0001
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "mid_open": opened,
            "mid_high": opened + 0.00005,
            "mid_low": opened - 0.00005,
            "mid_close": opened + 0.00002,
            "tick_count": 10,
            "spread_median_pips": 1.0,
        }
    )


def london_calendar() -> pd.DataFrame:
    start = datetime(2024, 1, 2, tzinfo=UTC)
    calendar = build_session_calendar(start, start + timedelta(days=1), CONFIG.sessions)
    return calendar[calendar["session_name"].eq("london")]


def build(bars: pd.DataFrame) -> pd.DataFrame:
    return build_event_dataset(
        bars,
        london_calendar(),
        pip_size=0.0001,
        preopen_windows=(60,),
        horizons=(15,),
        minimum_coverage_ratio=0.95,
    )


def test_event_metrics_use_exact_half_open_windows() -> None:
    event = build(synthetic_bars()).iloc[0]

    assert event["eligible"]
    assert event["open_price_mid"] == pytest.approx(1.2012)
    assert event["pre_60_range_pips"] == pytest.approx(12.0)
    assert event["pre_60_return_pips"] == pytest.approx(11.2)
    assert event["fwd_15_range_pips"] == pytest.approx(3.0)
    assert event["fwd_15_return_pips"] == pytest.approx(2.2)
    assert event["fwd_15_up_excursion_pips"] == pytest.approx(2.5)
    assert event["fwd_15_down_excursion_pips"] == pytest.approx(0.5)
    assert event["fwd_15_range_over_pre60"] == pytest.approx(0.25)


def test_data_at_horizon_end_cannot_change_event_labels() -> None:
    bars = synthetic_bars()
    original = build(bars).iloc[0]
    bars.loc[bars["timestamp"].ge(pd.Timestamp("2024-01-02 08:15Z")), "mid_high"] = 9
    changed = build(bars).iloc[0]

    assert changed["fwd_15_range_pips"] == original["fwd_15_range_pips"]
    assert changed["fwd_15_return_pips"] == original["fwd_15_return_pips"]


def test_missing_bar_makes_event_ineligible() -> None:
    bars = synthetic_bars()
    bars = bars[bars["timestamp"].ne(pd.Timestamp("2024-01-02 08:05Z"))]

    event = build(bars).iloc[0]

    assert not event["eligible"]
    assert event["exclusion_reason"] == "insufficient_window_coverage"
