"""Deterministic fixed-time and matched-random control construction."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from gbpusd_research.config import SessionsConfig
from gbpusd_research.features.sessions import fx_trading_day


def build_fixed_control_calendar(
    session_calendar: pd.DataFrame, config: SessionsConfig
) -> pd.DataFrame:
    rows = []
    for event in session_calendar.itertuples(index=False):
        session = config.sessions[event.session_name]
        local_time = config.controls.fixed_local_times[event.session_name]
        local = datetime.combine(
            event.local_session_date,
            local_time,
            tzinfo=ZoneInfo(session.timezone),
        )
        opened = pd.Timestamp(local.astimezone(UTC))
        offset = local.utcoffset()
        dst = local.dst()
        rows.append(
            {
                "session_name": event.session_name,
                "local_session_date": event.local_session_date,
                "open_timestamp_utc": opened,
                "open_timestamp_local": local.isoformat(),
                "window_end_utc": opened + timedelta(minutes=session.study_minutes),
                "study_minutes": session.study_minutes,
                "utc_offset_minutes": int(offset.total_seconds() // 60)
                if offset
                else 0,
                "is_dst": bool(dst and dst.total_seconds()),
                "weekday": event.local_session_date.weekday(),
                "fx_trading_day": fx_trading_day(opened, config.trading_day),
                "matched_event_id": (
                    f"session_open:{event.session_name}:"
                    f"{event.local_session_date.isoformat()}"
                ),
            }
        )
    return pd.DataFrame(rows)


def _forbidden_candidates(
    timestamps: pd.Series,
    session_calendar: pd.DataFrame,
    *,
    preopen_minutes: int,
    forward_minutes: int,
    exclusion_minutes: int,
) -> np.ndarray:
    values = timestamps.astype("int64").to_numpy()
    difference = np.zeros(len(values) + 1, dtype=np.int32)
    for opened in pd.to_datetime(session_calendar["open_timestamp_utc"], utc=True):
        lower = opened - timedelta(minutes=exclusion_minutes + forward_minutes)
        upper = opened + timedelta(minutes=exclusion_minutes + preopen_minutes)
        left = np.searchsorted(values, lower.value, side="left")
        right = np.searchsorted(values, upper.value, side="right")
        difference[left] += 1
        difference[right] -= 1
    return np.cumsum(difference[:-1]) > 0


def build_matched_control_calendar(
    bars: pd.DataFrame,
    session_calendar: pd.DataFrame,
    config: SessionsConfig,
    *,
    preopen_minutes: int,
    forward_minutes: int,
    random_seed: int,
) -> pd.DataFrame:
    """Select one reproducible non-opening M5 start per opening event."""

    timestamps = (
        pd.to_datetime(bars["timestamp"], utc=True).sort_values().drop_duplicates()
    )
    forbidden = _forbidden_candidates(
        timestamps,
        session_calendar,
        preopen_minutes=preopen_minutes,
        forward_minutes=forward_minutes,
        exclusion_minutes=config.controls.exclusion_minutes_around_session_open,
    )
    lower_bound = timestamps.iloc[0] + timedelta(minutes=preopen_minutes)
    upper_bound = timestamps.iloc[-1] - timedelta(minutes=forward_minutes)
    base = pd.DataFrame({"timestamp": timestamps.to_numpy(), "forbidden": forbidden})
    base = base[
        base["timestamp"].ge(lower_bound)
        & base["timestamp"].le(upper_bound)
        & ~base["forbidden"]
    ].copy()

    rows = []
    used: set[pd.Timestamp] = set()
    for event in session_calendar.itertuples(index=False):
        session = config.sessions[event.session_name]
        local = base["timestamp"].dt.tz_convert(session.timezone)
        candidates = base[
            local.dt.year.eq(event.local_session_date.year)
            & local.dt.month.eq(event.local_session_date.month)
            & local.dt.weekday.eq(event.local_session_date.weekday())
        ]["timestamp"].tolist()
        candidates = [candidate for candidate in candidates if candidate not in used]
        if not candidates:
            continue
        event_id = (
            f"session_open:{event.session_name}:{event.local_session_date.isoformat()}"
        )
        digest = hashlib.sha256(f"{random_seed}:{event_id}".encode()).digest()
        selected = candidates[int.from_bytes(digest[:8], "big") % len(candidates)]
        used.add(selected)
        selected_local = selected.tz_convert(session.timezone).to_pydatetime()
        offset = selected_local.utcoffset()
        dst = selected_local.dst()
        rows.append(
            {
                "session_name": event.session_name,
                "local_session_date": event.local_session_date,
                "open_timestamp_utc": selected,
                "open_timestamp_local": selected_local.isoformat(),
                "window_end_utc": selected + timedelta(minutes=session.study_minutes),
                "study_minutes": session.study_minutes,
                "utc_offset_minutes": int(offset.total_seconds() // 60)
                if offset
                else 0,
                "is_dst": bool(dst and dst.total_seconds()),
                "weekday": event.local_session_date.weekday(),
                "fx_trading_day": fx_trading_day(selected, config.trading_day),
                "matched_event_id": event_id,
            }
        )
    return pd.DataFrame(rows)
