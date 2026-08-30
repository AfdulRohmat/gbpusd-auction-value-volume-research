from datetime import UTC, datetime

import pandas as pd
import pytest

from gbpusd_research.data.resample import resample_ticks_m5
from gbpusd_research.data.validation import validate_m5, validate_ticks


def sample_ticks() -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    datetime(2024, 1, 2, 8, 0, tzinfo=UTC),
                    datetime(2024, 1, 2, 8, 4, 59, tzinfo=UTC),
                    datetime(2024, 1, 2, 8, 5, tzinfo=UTC),
                ],
                utc=True,
            ),
            "bid": [1.2700, 1.2710, 1.2705],
            "ask": [1.2702, 1.2713, 1.2707],
            "activity": [1, 1, 1],
        }
    )
    frame["mid"] = (frame["bid"] + frame["ask"]) / 2
    frame["spread_pips"] = (frame["ask"] - frame["bid"]) / 0.0001
    frame["source_archive"] = "test.zip"
    return frame


def test_m5_boundaries_and_ohlc() -> None:
    bars = resample_ticks_m5(sample_ticks())

    assert len(bars) == 2
    assert bars.loc[0, "timestamp"] == pd.Timestamp("2024-01-02 08:00", tz="UTC")
    assert bars.loc[0, "bid_open"] == pytest.approx(1.2700)
    assert bars.loc[0, "bid_high"] == pytest.approx(1.2710)
    assert bars.loc[0, "bid_close"] == pytest.approx(1.2710)
    assert bars.loc[0, "tick_count"] == 2
    assert bars.loc[1, "tick_count"] == 1
    assert bars.loc[0, "mid_activity_sum"] == pytest.approx(1.2701 + 1.27115)
    assert bars.loc[0, "mid_squared_activity_sum"] == pytest.approx(
        1.2701**2 + 1.27115**2
    )
    assert bars.loc[0, "up_quote_count"] == 1
    assert bars.loc[0, "down_quote_count"] == 0
    assert bars.loc[1, "down_quote_count"] == 1


def test_tick_and_bar_quality() -> None:
    ticks = sample_ticks()
    bars = resample_ticks_m5(ticks)

    assert validate_ticks(ticks, max_spread_pips=10)["valid"] is True
    assert validate_m5(bars)["valid"] is True


def test_resample_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="empty tick dataset"):
        resample_ticks_m5(pd.DataFrame())
