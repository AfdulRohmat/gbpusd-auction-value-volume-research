from datetime import date, time

import pandas as pd
import pytest

from gbpusd_research.config import TradingDayConfig
from gbpusd_research.features.sessions import fx_trading_day
from gbpusd_research.features.vwap import (
    assign_fx_trading_days,
    attach_event_vwap,
    enrich_fx_day_vwap,
)

TRADING_DAY = TradingDayConfig(timezone="America/New_York", boundary=time(17))


def _bars() -> pd.DataFrame:
    timestamps = pd.to_datetime(
        [
            "2024-01-08 13:20:00Z",
            "2024-01-08 13:25:00Z",
            "2024-01-08 13:50:00Z",
            "2024-01-08 13:55:00Z",
            "2024-01-08 14:00:00Z",
        ]
    )
    prices = [1.1000, 1.1010, 1.1020, 1.1030, 9.0000]
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "activity_count": [1] * len(timestamps),
            "mid_activity_sum": prices,
            "mid_squared_activity_sum": [price**2 for price in prices],
        }
    )


def test_vectorized_fx_day_matches_scalar_at_dst_and_boundary() -> None:
    timestamps = pd.Series(
        pd.to_datetime(
            [
                "2024-01-08 21:59:59Z",
                "2024-01-08 22:00:00Z",
                "2024-07-08 20:59:59Z",
                "2024-07-08 21:00:00Z",
            ]
        )
    )

    result = assign_fx_trading_days(timestamps, TRADING_DAY)
    expected = timestamps.map(lambda timestamp: fx_trading_day(timestamp, TRADING_DAY))

    assert result.tolist() == expected.tolist()
    assert result.tolist() == [
        date(2024, 1, 8),
        date(2024, 1, 9),
        date(2024, 7, 8),
        date(2024, 7, 9),
    ]


def test_event_vwap_uses_only_completed_m5_bars() -> None:
    enriched = enrich_fx_day_vwap(
        _bars(), TRADING_DAY, pip_size=0.0001, slope_minutes=30
    )
    events = pd.DataFrame(
        {
            "event_timestamp_utc": pd.to_datetime(["2024-01-08 14:00:00Z"]),
            "fx_trading_day": [date(2024, 1, 8)],
            "open_price_mid": [1.1040],
        }
    )

    result = attach_event_vwap(
        events, enriched, pip_size=0.0001, boundary_buffer_pips=1.0
    ).iloc[0]

    assert result["fx_day_vwap"] == pytest.approx(
        (1.1000 + 1.1010 + 1.1020 + 1.1030) / 4
    )
    assert result["vwap_slope_pips"] == pytest.approx(10.0)
    assert result["vwap_available_at"] == pd.Timestamp("2024-01-08 14:00:00Z")
    assert result["vwap_state"] == "above_vwap"


def test_future_bar_cannot_change_event_time_vwap() -> None:
    full = enrich_fx_day_vwap(_bars(), TRADING_DAY, pip_size=0.0001, slope_minutes=30)
    without_future = full.iloc[:-1].copy()
    events = pd.DataFrame(
        {
            "event_timestamp_utc": pd.to_datetime(["2024-01-08 14:00:00Z"]),
            "fx_trading_day": [date(2024, 1, 8)],
            "open_price_mid": [1.1040],
        }
    )

    with_future = attach_event_vwap(
        events, full, pip_size=0.0001, boundary_buffer_pips=1.0
    )
    without = attach_event_vwap(
        events, without_future, pip_size=0.0001, boundary_buffer_pips=1.0
    )

    assert with_future.loc[0, "fx_day_vwap"] == without.loc[0, "fx_day_vwap"]
