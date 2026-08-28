"""DST-aware session calendars and M5 window tagging."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from gbpusd_research.config import SessionsConfig, TradingDayConfig


def fx_trading_day(
    timestamp: datetime | pd.Timestamp, config: TradingDayConfig
) -> date:
    """Return the New-York-close trading-day label for an aware timestamp.

    Observations at or after the configured boundary belong to the following
    trading day. For example, Sunday 18:00 New York belongs to Monday.
    """

    value = pd.Timestamp(timestamp)
    if value.tzinfo is None:
        raise ValueError("FX trading-day timestamp must be timezone-aware")
    local = value.to_pydatetime().astimezone(ZoneInfo(config.timezone))
    label = local.date()
    if local.time().replace(tzinfo=None) >= config.boundary:
        label += timedelta(days=1)
    return label


def build_session_calendar(
    start: datetime,
    end: datetime,
    config: SessionsConfig,
    *,
    weekdays_only: bool = True,
) -> pd.DataFrame:
    """Build events whose UTC opens fall inside the half-open interval."""

    start_utc = pd.Timestamp(start)
    end_utc = pd.Timestamp(end)
    if start_utc.tzinfo is None or end_utc.tzinfo is None:
        raise ValueError("Session calendar bounds must be timezone-aware")
    start_utc = start_utc.tz_convert("UTC")
    end_utc = end_utc.tz_convert("UTC")
    if end_utc <= start_utc:
        raise ValueError("Session calendar end must be later than start")

    first_date = start_utc.date() - timedelta(days=1)
    last_date = end_utc.date() + timedelta(days=1)
    rows = []
    current = first_date
    while current <= last_date:
        for session_name, session in config.sessions.items():
            if weekdays_only and current.weekday() >= 5:
                continue
            timezone = ZoneInfo(session.timezone)
            local_open = datetime.combine(current, session.open, tzinfo=timezone)
            open_utc = pd.Timestamp(local_open.astimezone(UTC))
            if not start_utc <= open_utc < end_utc:
                continue
            offset = local_open.utcoffset()
            dst = local_open.dst()
            rows.append(
                {
                    "session_name": session_name,
                    "local_session_date": current,
                    "open_timestamp_utc": open_utc,
                    "open_timestamp_local": local_open.isoformat(),
                    "window_end_utc": open_utc
                    + timedelta(minutes=session.study_minutes),
                    "study_minutes": session.study_minutes,
                    "utc_offset_minutes": int(offset.total_seconds() // 60)
                    if offset
                    else 0,
                    "is_dst": bool(dst and dst.total_seconds()),
                    "weekday": current.weekday(),
                    "fx_trading_day": fx_trading_day(open_utc, config.trading_day),
                }
            )
        current += timedelta(days=1)

    calendar = pd.DataFrame(rows)
    if not calendar.empty:
        calendar = calendar.sort_values(
            ["open_timestamp_utc", "session_name"], kind="stable"
        ).reset_index(drop=True)
    return calendar


def tag_session_windows(bars: pd.DataFrame, calendar: pd.DataFrame) -> pd.DataFrame:
    """Attach configured session labels to bars in `[open, window_end)`."""

    if "timestamp" not in bars:
        raise ValueError("M5 bars must contain timestamp")
    tagged = bars.copy()
    timestamps = pd.to_datetime(tagged["timestamp"], utc=True)
    tagged["timestamp"] = timestamps
    tagged["session_name"] = pd.Series(pd.NA, index=tagged.index, dtype="string")
    tagged["session_local_date"] = pd.Series(pd.NA, index=tagged.index, dtype="string")
    tagged["session_open_utc"] = pd.Series(
        pd.NaT, index=tagged.index, dtype="datetime64[ns, UTC]"
    )
    tagged["minutes_from_session_open"] = pd.Series(
        pd.NA, index=tagged.index, dtype="Int64"
    )

    for event in calendar.itertuples(index=False):
        opened = pd.Timestamp(event.open_timestamp_utc)
        ended = pd.Timestamp(event.window_end_utc)
        mask = timestamps.ge(opened) & timestamps.lt(ended)
        if tagged.loc[mask, "session_name"].notna().any():
            raise ValueError(f"Overlapping configured session windows at {opened}")
        tagged.loc[mask, "session_name"] = event.session_name
        tagged.loc[mask, "session_local_date"] = event.local_session_date.isoformat()
        tagged.loc[mask, "session_open_utc"] = opened
        minutes = (
            (timestamps.loc[mask] - opened)
            .dt.total_seconds()
            .floordiv(60)
            .astype("int64")
        )
        tagged.loc[mask, "minutes_from_session_open"] = minutes.array
    return tagged


def session_coverage(
    bars: pd.DataFrame,
    calendar: pd.DataFrame,
    *,
    minimum_ratio: float,
) -> pd.DataFrame:
    if not 0 < minimum_ratio <= 1:
        raise ValueError("minimum_ratio must be in (0, 1]")
    timestamps = pd.to_datetime(bars["timestamp"], utc=True)
    rows = []
    for event in calendar.itertuples(index=False):
        opened = pd.Timestamp(event.open_timestamp_utc)
        ended = pd.Timestamp(event.window_end_utc)
        observed = int(
            timestamps[timestamps.ge(opened) & timestamps.lt(ended)].nunique()
        )
        expected = event.study_minutes // 5
        ratio = observed / expected
        rows.append(
            {
                "session_name": event.session_name,
                "local_session_date": event.local_session_date,
                "open_timestamp_utc": opened,
                "expected_bars": expected,
                "observed_bars": observed,
                "coverage_ratio": ratio,
                "eligible": ratio >= minimum_ratio,
            }
        )
    return pd.DataFrame(rows)
