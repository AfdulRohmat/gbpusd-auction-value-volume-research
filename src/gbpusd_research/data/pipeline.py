"""One-day market-data vertical slice."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from gbpusd_research.config import ProjectConfig
from gbpusd_research.data.histdata import archive_path, read_archive_for_utc_day
from gbpusd_research.data.resample import resample_ticks_m5
from gbpusd_research.data.validation import validate_m5, validate_ticks
from gbpusd_research.features.sessions import (
    build_session_calendar,
    session_coverage,
    tag_session_windows,
)
from gbpusd_research.utils.paths import resolve_within_project


def _atomic_parquet(frame: pd.DataFrame, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    frame.to_parquet(temporary, index=False)
    temporary.replace(destination)


def _atomic_json(content: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_text(
        json.dumps(content, indent=2, sort_keys=True), encoding="utf-8"
    )
    temporary.replace(destination)


def build_day(project_root: Path, config: ProjectConfig, day: date) -> dict[str, Any]:
    research = config.research
    raw_root = resolve_within_project(project_root, research.data.paths.raw)
    interim_root = resolve_within_project(project_root, research.data.paths.interim)
    processed_root = resolve_within_project(project_root, research.data.paths.processed)
    symbol = research.instrument.symbol

    frames = []
    source_files = []
    local_dates = (day - timedelta(days=1), day)
    required_archives = {
        archive_path(raw_root, symbol, local_day.year, local_day.month)
        for local_day in local_dates
    }
    missing_archives = [path for path in required_archives if not path.is_file()]
    if missing_archives:
        missing = ", ".join(
            str(path.relative_to(project_root)) for path in missing_archives
        )
        raise ValueError(f"Missing HistData archive(s) required for UTC day: {missing}")
    for path in sorted(required_archives):
        source_files.append(str(path.relative_to(project_root)))
        frame = read_archive_for_utc_day(
            path,
            day,
            pip_size=research.instrument.pip_size,
        )
        if not frame.empty:
            frames.append(frame)
    if not frames:
        raise ValueError(f"No HistData ticks found for UTC day {day.isoformat()}")

    ticks = pd.concat(frames, ignore_index=True)
    ticks = (
        ticks.sort_values("timestamp", kind="stable")
        .drop_duplicates()
        .reset_index(drop=True)
    )
    tick_quality = validate_ticks(
        ticks, max_spread_pips=research.quality.max_spread_pips_warning
    )
    if not tick_quality["valid"]:
        raise ValueError(f"Tick quality validation failed: {tick_quality}")

    bars = resample_ticks_m5(ticks)
    m5_quality = validate_m5(bars)
    if not m5_quality["valid"]:
        raise ValueError(f"M5 quality validation failed: {m5_quality}")

    partition = (
        Path(f"symbol={symbol}") / f"year={day.year:04d}" / f"month={day.month:02d}"
    )
    tick_path = interim_root / partition / f"ticks-{day.isoformat()}.parquet"
    m5_path = processed_root / "m5" / partition / f"m5-{day.isoformat()}.parquet"
    quality_path = (
        processed_root / "quality" / partition / f"quality-{day.isoformat()}.json"
    )
    _atomic_parquet(ticks, tick_path)
    _atomic_parquet(bars, m5_path)

    summary = {
        "symbol": symbol,
        "day": day.isoformat(),
        "generated_at": datetime.now(UTC).isoformat(),
        "source_files": source_files,
        "tick_output": str(tick_path.relative_to(project_root)),
        "m5_output": str(m5_path.relative_to(project_root)),
        "tick_quality": tick_quality,
        "m5_quality": m5_quality,
    }
    _atomic_json(summary, quality_path)
    summary["quality_output"] = str(quality_path.relative_to(project_root))
    return summary


def tag_day_sessions(
    project_root: Path, config: ProjectConfig, day: date
) -> dict[str, Any]:
    """Tag one UTC M5 day and write its calendar and coverage artifacts."""

    research = config.research
    processed_root = resolve_within_project(project_root, research.data.paths.processed)
    symbol = research.instrument.symbol
    partition = (
        Path(f"symbol={symbol}") / f"year={day.year:04d}" / f"month={day.month:02d}"
    )
    m5_path = processed_root / "m5" / partition / f"m5-{day.isoformat()}.parquet"
    if not m5_path.is_file():
        raise ValueError(
            f"M5 input does not exist: {m5_path.relative_to(project_root)}"
        )

    bars = pd.read_parquet(m5_path)
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    end = start + timedelta(days=1)
    calendar = build_session_calendar(start, end, config.sessions)
    tagged = tag_session_windows(bars, calendar)
    coverage = session_coverage(
        bars,
        calendar,
        minimum_ratio=research.quality.event_min_coverage_ratio,
    )

    tagged_path = (
        processed_root
        / "session_m5"
        / partition
        / f"session-m5-{day.isoformat()}.parquet"
    )
    calendar_path = (
        processed_root / "sessions" / partition / f"calendar-{day.isoformat()}.parquet"
    )
    coverage_path = (
        processed_root / "sessions" / partition / f"coverage-{day.isoformat()}.json"
    )
    _atomic_parquet(tagged, tagged_path)
    _atomic_parquet(calendar, calendar_path)
    coverage_records = json.loads(coverage.to_json(orient="records", date_format="iso"))
    coverage_content = {
        "symbol": symbol,
        "day": day.isoformat(),
        "generated_at": datetime.now(UTC).isoformat(),
        "events": coverage_records,
    }
    _atomic_json(coverage_content, coverage_path)
    return {
        "symbol": symbol,
        "day": day.isoformat(),
        "calendar_output": str(calendar_path.relative_to(project_root)),
        "tagged_m5_output": str(tagged_path.relative_to(project_root)),
        "coverage_output": str(coverage_path.relative_to(project_root)),
        "session_events": len(calendar),
        "tagged_bars": int(tagged["session_name"].notna().sum()),
        "all_events_eligible": bool(coverage["eligible"].all()),
        "coverage": coverage_records,
    }
