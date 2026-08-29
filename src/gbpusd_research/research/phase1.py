"""Orchestration for the reproducible Phase-1 opening event study."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from gbpusd_research.config import ProjectConfig
from gbpusd_research.data.pipeline import iter_months, load_m5_range
from gbpusd_research.features.sessions import build_session_calendar
from gbpusd_research.research.controls import (
    build_fixed_control_calendar,
    build_matched_control_calendar,
)
from gbpusd_research.research.event_study import build_event_dataset
from gbpusd_research.research.report import create_figures, render_markdown
from gbpusd_research.research.statistics import (
    descriptive_summary,
    paired_bootstrap_comparisons,
    summary_by_year,
)
from gbpusd_research.utils.paths import resolve_within_project


def _write_json(content: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(content, indent=2, sort_keys=True), encoding="utf-8")


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _git_commit(project_root: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _data_quality(
    project_root: Path,
    config: ProjectConfig,
    bars: pd.DataFrame,
    openings: pd.DataFrame,
    controls: pd.DataFrame,
) -> tuple[dict[str, Any], str]:
    research = config.research
    processed_root = resolve_within_project(project_root, research.data.paths.processed)
    raw_root = resolve_within_project(project_root, research.data.paths.raw)
    symbol = research.instrument.symbol
    months = []
    source_archives = []
    for year, month in iter_months(research.data.start, research.data.end):
        quality_path = (
            processed_root
            / "quality_monthly"
            / f"symbol={symbol}"
            / f"year={year:04d}"
            / f"quality-{year:04d}-{month:02d}.json"
        )
        if not quality_path.is_file():
            raise ValueError(f"Missing monthly quality file: {quality_path}")
        quality = json.loads(quality_path.read_text(encoding="utf-8"))
        months.append(quality)
        manifest_path = raw_root / "manifests" / f"{symbol}-{year:04d}-{month:02d}.json"
        if not manifest_path.is_file():
            raise ValueError(f"Missing source manifest: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source_archives.append(
            {
                "month": f"{year:04d}-{month:02d}",
                "archive_sha256": manifest["sha256"],
                "byte_size": manifest["byte_size"],
                "uncompressed_bytes": manifest["uncompressed_bytes"],
            }
        )
    combined_manifest_hash = _hash_text(json.dumps(source_archives, sort_keys=True))
    eligible = pd.concat([openings, controls], ignore_index=True)
    source_validation_passed = all(
        item["tick_quality"]["valid"] and item["m5_quality"]["valid"] for item in months
    )
    coverage = (
        openings.groupby(["calendar_year", "session_name"], observed=True)
        .agg(scheduled=("event_id", "size"), eligible=("eligible", "sum"))
        .reset_index()
    )
    coverage["eligible_ratio"] = coverage["eligible"] / coverage["scheduled"]
    coverage_gate_passed = bool((coverage["eligible_ratio"] >= 0.90).all())
    payload = {
        "valid": source_validation_passed and coverage_gate_passed,
        "source_validation_passed": source_validation_passed,
        "phase1_coverage_gate_passed": coverage_gate_passed,
        "opening_coverage_by_session_year": coverage.to_dict(orient="records"),
        "monthly_quality": months,
        "source_archives": source_archives,
        "source_manifest_set_sha256": combined_manifest_hash,
        "totals": {
            "source_ticks": sum(item["tick_quality"]["row_count"] for item in months),
            "loaded_m5_bars": len(bars),
            "excessive_spread_ticks": sum(
                item["tick_quality"]["excessive_spread_count"] for item in months
            ),
            "events": len(eligible),
            "eligible_events": int(eligible["eligible"].sum()),
            "excluded_events": int((~eligible["eligible"]).sum()),
        },
        "loaded_interval": {
            "first_bar": bars["timestamp"].min().isoformat(),
            "last_bar": bars["timestamp"].max().isoformat(),
            "median_m5_spread_pips": float(bars["spread_median_pips"].median()),
            "p95_m5_spread_pips": float(bars["spread_median_pips"].quantile(0.95)),
        },
    }
    return payload, combined_manifest_hash


def _evaluate_research_gate(
    openings: pd.DataFrame,
    comparisons: pd.DataFrame,
    data_quality: dict[str, Any],
) -> dict[str, Any]:
    aggregate = comparisons[
        comparisons["analysis_scope"].eq("all")
        & comparisons["horizon_minutes"].eq(60)
        & comparisons["metric"].eq("range_pips")
    ]
    yearly = comparisons[
        comparisons["analysis_scope"].eq("calendar_year")
        & comparisons["horizon_minutes"].eq(60)
        & comparisons["metric"].eq("range_pips")
    ]
    aggregate_ci_passed = bool(
        len(aggregate) == 4 and (aggregate["mean_ci_low"] > 0).all()
    )
    expected_years = openings["calendar_year"].nunique()
    expected_session_years = expected_years * openings["session_name"].nunique()
    actual_session_years = yearly.groupby(
        ["calendar_year", "session_name"], observed=True
    ).ngroups
    yearly_groups_complete = bool(
        actual_session_years == expected_session_years
        and len(yearly) == expected_session_years * 2
    )
    yearly_direction_passed = yearly_groups_complete
    yearly_materiality_passed = yearly_groups_complete
    for _, group in yearly.groupby(["calendar_year", "session_name"], observed=True):
        yearly_direction_passed &= bool((group["mean_difference"] > 0).all())
        yearly_materiality_passed &= bool((group["mean_difference"] >= 3).any())
    eligible = openings[openings["eligible"]]
    spread_difference = float(
        (eligible["fwd_5_spread_median_pips"] - eligible["pre_30_spread_median_pips"])
        .abs()
        .median()
    )
    spread_gate_passed = spread_difference <= 1.0
    checks = {
        "source_validation": bool(data_quality["source_validation_passed"]),
        "opening_coverage_by_session_year": bool(
            data_quality["phase1_coverage_gate_passed"]
        ),
        "aggregate_confidence_intervals_above_zero": aggregate_ci_passed,
        "yearly_control_directions_positive": yearly_direction_passed,
        "yearly_materiality_at_least_3_pips": yearly_materiality_passed,
        "median_open_spread_change_at_most_1_pip": spread_gate_passed,
        "multiple_calendar_years": expected_years >= 2,
    }
    development_checks = {
        name: passed
        for name, passed in checks.items()
        if name != "multiple_calendar_years"
    }
    return {
        "passed": all(checks.values()),
        "development_passed": all(development_checks.values()),
        "checks": checks,
        "median_absolute_open_spread_change_pips": spread_difference,
    }


def run_phase1(project_root: Path, config: ProjectConfig) -> dict[str, Any]:
    research = config.research
    start = research.data.start
    end = research.data.end
    bars = load_m5_range(project_root, config, start, end)
    start_utc = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    end_utc = datetime.combine(end, datetime.min.time(), tzinfo=UTC)
    calendar = build_session_calendar(start_utc, end_utc, config.sessions)
    common = {
        "pip_size": research.instrument.pip_size,
        "preopen_windows": research.study.preopen_windows_minutes,
        "horizons": research.study.horizons_minutes,
        "minimum_coverage_ratio": research.quality.event_min_coverage_ratio,
    }
    openings = build_event_dataset(bars, calendar, event_kind="session_open", **common)

    fixed_calendar = build_fixed_control_calendar(calendar, config.sessions)
    fixed = build_event_dataset(
        bars, fixed_calendar, event_kind="fixed_control", **common
    )
    matched_calendar = build_matched_control_calendar(
        bars,
        calendar,
        config.sessions,
        preopen_minutes=max(research.study.preopen_windows_minutes),
        forward_minutes=max(research.study.horizons_minutes),
        random_seed=research.study.random_seed,
    )
    matched = build_event_dataset(
        bars, matched_calendar, event_kind="matched_control", **common
    )
    controls = pd.concat([fixed, matched], ignore_index=True)
    all_events = pd.concat([openings, controls], ignore_index=True)

    overall = descriptive_summary(all_events, research.study.horizons_minutes)
    yearly = summary_by_year(all_events, research.study.horizons_minutes)
    comparisons = paired_bootstrap_comparisons(
        openings,
        controls,
        horizons=research.study.horizons_minutes,
        resamples=research.study.bootstrap_resamples,
        confidence_level=research.study.confidence_level,
        random_seed=research.study.random_seed,
    )
    weekday = (
        all_events[all_events["eligible"]]
        .groupby(["event_kind", "session_name", "weekday"], observed=True)
        .agg(
            count=("event_id", "size"),
            mean_60m_range_pips=("fwd_60_range_pips", "mean"),
            median_60m_range_pips=("fwd_60_range_pips", "median"),
        )
        .reset_index()
    )

    config_json = json.dumps(config.model_dump(mode="json"), sort_keys=True)
    run_id = f"{start:%Y%m%d}_{end:%Y%m%d}_{_hash_text(config_json)[:8]}"
    processed_root = resolve_within_project(project_root, research.data.paths.processed)
    output = processed_root / "reports" / "phase1" / run_id
    figures = output / "figures"
    output.mkdir(parents=True, exist_ok=True)

    openings.to_parquet(output / "events.parquet", index=False)
    controls.to_parquet(output / "controls.parquet", index=False)
    all_events[~all_events["eligible"]].to_parquet(
        output / "event_exclusions.parquet", index=False
    )
    overall.to_csv(output / "summary_overall.csv", index=False)
    yearly.to_csv(output / "summary_by_year.csv", index=False)
    weekday.to_csv(output / "summary_by_weekday.csv", index=False)
    comparisons.to_csv(output / "statistical_comparisons.csv", index=False)
    data_quality, source_manifest_hash = _data_quality(
        project_root, config, bars, openings, controls
    )
    research_gate = _evaluate_research_gate(openings, comparisons, data_quality)
    data_quality["research_gate"] = research_gate
    _write_json(data_quality, output / "data_quality.json")
    figure_paths = create_figures(all_events, bars, figures)
    report = render_markdown(
        openings,
        controls,
        comparisons,
        data_quality=data_quality,
        start=start.isoformat(),
        end=end.isoformat(),
    )
    (output / "report.md").write_text(report, encoding="utf-8")

    manifest = {
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "interval": {"start": start.isoformat(), "end_exclusive": end.isoformat()},
        "config_sha256": _hash_text(config_json),
        "config": config.model_dump(mode="json"),
        "source_manifest_set_sha256": source_manifest_hash,
        "research_gate": research_gate,
        "git_commit": _git_commit(project_root),
        "runtime": {
            "python": platform.python_version(),
            "packages": {
                package: importlib.metadata.version(package)
                for package in ("numpy", "pandas", "pyarrow", "scipy", "matplotlib")
            },
        },
        "random_seed": research.study.random_seed,
        "bootstrap_resamples": research.study.bootstrap_resamples,
        "rows": {
            "m5": len(bars),
            "openings": len(openings),
            "eligible_openings": int(openings["eligible"].sum()),
            "controls": len(controls),
            "eligible_controls": int(controls["eligible"].sum()),
            "comparisons": len(comparisons),
            "exclusions": int((~all_events["eligible"]).sum()),
            "summary_overall": len(overall),
            "summary_by_year": len(yearly),
            "summary_by_weekday": len(weekday),
        },
        "figures": [str(path.relative_to(output)) for path in figure_paths],
    }
    _write_json(manifest, output / "run_manifest.json")
    return {
        **manifest,
        "output_directory": str(output.relative_to(project_root)),
        "report": str((output / "report.md").relative_to(project_root)),
    }
