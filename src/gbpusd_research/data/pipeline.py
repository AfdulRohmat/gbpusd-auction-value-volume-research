"""One-day market-data vertical slice."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from gbpusd_research.config import ProjectConfig
from gbpusd_research.data.histdata import (
    archive_path,
    read_archive,
    read_archive_for_utc_day,
)
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


def iter_months(start: date, end: date) -> list[tuple[int, int]]:
    """Return calendar months intersecting the half-open date interval."""

    if end <= start:
        raise ValueError("Range end must be later than start")
    months = []
    current = date(start.year, start.month, 1)
    while current < end:
        months.append((current.year, current.month))
        current = (
            date(current.year + 1, 1, 1)
            if current.month == 12
            else date(current.year, current.month + 1, 1)
        )
    return months


def build_month_m5(
    project_root: Path,
    config: ProjectConfig,
    year: int,
    month: int,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Decode one source month directly to an auditable monthly M5 file."""

    research = config.research
    raw_root = resolve_within_project(project_root, research.data.paths.raw)
    processed_root = resolve_within_project(project_root, research.data.paths.processed)
    symbol = research.instrument.symbol
    source = archive_path(raw_root, symbol, year, month)
    if not source.is_file():
        raise ValueError(
            f"Missing HistData archive: {source.relative_to(project_root)}"
        )

    partition = Path(f"symbol={symbol}") / f"year={year:04d}"
    m5_path = (
        processed_root / "m5_monthly" / partition / f"m5-{year:04d}-{month:02d}.parquet"
    )
    quality_path = (
        processed_root
        / "quality_monthly"
        / partition
        / f"quality-{year:04d}-{month:02d}.json"
    )
    if m5_path.is_file() and quality_path.is_file() and not force:
        summary = json.loads(quality_path.read_text(encoding="utf-8"))
        summary["status"] = "cached"
        return summary

    ticks = read_archive(source, pip_size=research.instrument.pip_size)
    ticks = (
        ticks.sort_values("timestamp", kind="stable")
        .drop_duplicates()
        .reset_index(drop=True)
    )
    tick_quality = validate_ticks(
        ticks, max_spread_pips=research.quality.max_spread_pips_warning
    )
    if not tick_quality["valid"]:
        raise ValueError(f"Monthly tick quality validation failed: {tick_quality}")
    bars = resample_ticks_m5(ticks)
    m5_quality = validate_m5(bars)
    if not m5_quality["valid"]:
        raise ValueError(f"Monthly M5 quality validation failed: {m5_quality}")
    _atomic_parquet(bars, m5_path)
    summary = {
        "status": "built",
        "symbol": symbol,
        "year": year,
        "month": month,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_file": str(source.relative_to(project_root)),
        "m5_output": str(m5_path.relative_to(project_root)),
        "tick_quality": tick_quality,
        "m5_quality": m5_quality,
    }
    _atomic_json(summary, quality_path)
    summary["quality_output"] = str(quality_path.relative_to(project_root))
    return summary


def load_m5_range(
    project_root: Path, config: ProjectConfig, start: date, end: date
) -> pd.DataFrame:
    """Load configured monthly M5 outputs and restrict them to `[start, end)`."""

    processed_root = resolve_within_project(
        project_root, config.research.data.paths.processed
    )
    symbol = config.research.instrument.symbol
    frames = []
    missing = []
    for year, month in iter_months(start, end):
        path = (
            processed_root
            / "m5_monthly"
            / f"symbol={symbol}"
            / f"year={year:04d}"
            / f"m5-{year:04d}-{month:02d}.parquet"
        )
        if not path.is_file():
            missing.append(str(path.relative_to(project_root)))
        else:
            frames.append(pd.read_parquet(path))
    if missing:
        raise ValueError("Missing monthly M5 file(s): " + ", ".join(missing))
    if not frames:
        raise ValueError("No monthly M5 data found")
    bars = pd.concat(frames, ignore_index=True)
    bars["timestamp"] = pd.to_datetime(bars["timestamp"], utc=True)
    start_utc = pd.Timestamp(datetime.combine(start, datetime.min.time(), tzinfo=UTC))
    end_utc = pd.Timestamp(datetime.combine(end, datetime.min.time(), tzinfo=UTC))
    return (
        bars[bars["timestamp"].ge(start_utc) & bars["timestamp"].lt(end_utc)]
        .sort_values("timestamp", kind="stable")
        .drop_duplicates(subset=["timestamp"], keep="last")
        .reset_index(drop=True)
    )


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
