"""Orchestration and reporting for the Phase-2 value-state study."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from gbpusd_research.config import ProjectConfig, ValueStateConfig
from gbpusd_research.data.pipeline import iter_months, load_m5_range
from gbpusd_research.features.sessions import build_session_calendar
from gbpusd_research.features.volume_profile import (
    attach_previous_profile,
    build_daily_tick_profiles,
)
from gbpusd_research.features.vwap import attach_event_vwap, enrich_fx_day_vwap
from gbpusd_research.research.event_study import build_event_dataset
from gbpusd_research.research.value_state import (
    attach_value_outcomes,
    conditional_statistics,
    continuous_feature_associations,
    value_state_comparisons,
)
from gbpusd_research.utils.paths import resolve_within_project


def _write_json(content: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(content, indent=2, sort_keys=True), encoding="utf-8")


def _hash_json(content: object) -> str:
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, default=str).encode()
    ).hexdigest()


def phase2_run_id(config: ProjectConfig, value_config: ValueStateConfig) -> str:
    """Return the deterministic report identifier for a Phase-2 configuration."""

    combined_config = {
        "project": config.model_dump(mode="json"),
        "value_state": value_config.model_dump(mode="json"),
    }
    config_hash = _hash_json(combined_config)
    research = config.research
    return f"{research.data.start:%Y%m%d}_{research.data.end:%Y%m%d}_{config_hash[:8]}"


def _git_commit(project_root: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _git_dirty(project_root: Path) -> bool | None:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(result.stdout.strip()) if result.returncode == 0 else None


def _source_snapshot(project_root: Path, config: ProjectConfig) -> list[dict[str, Any]]:
    research = config.research
    raw_root = resolve_within_project(project_root, research.data.paths.raw)
    rows = []
    for year, month in iter_months(research.data.start, research.data.end):
        path = (
            raw_root
            / "manifests"
            / f"{research.instrument.symbol}-{year:04d}-{month:02d}.json"
        )
        if not path.is_file():
            raise ValueError(
                f"Missing source manifest: {path.relative_to(project_root)}"
            )
        content = json.loads(path.read_text(encoding="utf-8"))
        rows.append(
            {
                "year_month": f"{year:04d}-{month:02d}",
                "manifest": str(path.relative_to(project_root)),
                "archive": content["relative_path"],
                "byte_size": content["byte_size"],
                "sha256": content["sha256"],
            }
        )
    return rows


def _evaluate_gate(
    events: pd.DataFrame,
    profiles: pd.DataFrame,
    comparisons: pd.DataFrame,
    value_config: ValueStateConfig,
    *,
    pip_size: float,
) -> dict[str, Any]:
    base = events[events["eligible"]]
    feature_coverage = float(events.loc[base.index, "value_eligible"].mean())
    minimum_group = value_config.research_gate.minimum_group_size
    sized = comparisons[
        comparisons["first_count"].ge(minimum_group)
        & (
            comparisons["second_count"].eq(0)
            | comparisons["second_count"].ge(minimum_group)
        )
    ]
    material = sized[
        sized["mean_difference"].abs().ge(value_config.research_gate.materiality_pips)
        & ((sized["ci_low"] > 0) | (sized["ci_high"] < 0))
    ]
    spread_change = float(
        (
            events.loc[events["value_eligible"], "fwd_5_spread_median_pips"]
            - events.loc[events["value_eligible"], "pre_30_spread_median_pips"]
        )
        .abs()
        .median()
    )
    profiled = events[events["profile_available"]]
    vwap = events[events["vwap_available"]]
    buffer_price = value_config.classification.boundary_buffer_pips * pip_size
    invariant_checks = {
        "event_ids_unique": bool(events["event_id"].is_unique),
        "profile_levels_ordered": bool(
            (
                (profiles["val"] <= profiles["poc"])
                & (profiles["poc"] <= profiles["vah"])
            ).all()
        ),
        "eligible_profile_coverage": bool(
            profiles.loc[profiles["eligible"], "m5_coverage_ratio"]
            .ge(value_config.profile.minimum_m5_coverage_ratio)
            .all()
        ),
        "previous_profile_strictly_prior": bool(
            (profiled["previous_profile_day"] < profiled["fx_trading_day"]).all()
        ),
        "vwap_available_before_event": bool(
            (vwap["vwap_available_at"] <= vwap["event_timestamp_utc"]).all()
        ),
        "above_value_boundary": bool(
            (
                profiled["value_state"].eq("above_value")
                == profiled["open_price_mid"].gt(
                    profiled["previous_vah"] + buffer_price
                )
            ).all()
        ),
        "below_value_boundary": bool(
            (
                profiled["value_state"].eq("below_value")
                == profiled["open_price_mid"].lt(
                    profiled["previous_val"] - buffer_price
                )
            ).all()
        ),
    }
    checks = {
        "feature_coverage": feature_coverage
        >= value_config.research_gate.minimum_feature_coverage_ratio,
        "eligible_daily_profiles_exist": bool(profiles["eligible"].any()),
        "point_in_time_invariants": all(invariant_checks.values()),
        "minimum_primary_group_size": not sized.empty,
        "material_state_contrast": not material.empty,
        "median_open_spread_change_at_most_1_pip": spread_change <= 1.0,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "invariant_checks": invariant_checks,
        "feature_coverage_ratio": feature_coverage,
        "material_contrasts": material[
            [
                "contrast",
                "session_name",
                "horizon_minutes",
                "mean_difference",
                "ci_low",
                "ci_high",
            ]
        ].to_dict(orient="records"),
        "median_absolute_open_spread_change_pips": spread_change,
    }


def _create_figures(events: pd.DataFrame, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = output_dir.parent / ".plot_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache_dir))
    os.environ.setdefault("XDG_CACHE_HOME", str(cache_dir))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    eligible = events[events["value_eligible"]].copy()
    paths = []

    groups = []
    labels = []
    for (session, state), group in eligible.groupby(
        ["session_name", "value_state"], observed=True
    ):
        groups.append(group["fwd_60_range_pips"].dropna())
        labels.append(f"{session}\n{state}")
    figure, axis = plt.subplots(figsize=(10, 5))
    axis.boxplot(groups, tick_labels=labels, showfliers=False)
    axis.set_ylabel("60-minute range (pips)")
    axis.set_title("Opening range by previous value state")
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    path = output_dir / "range_by_value_state.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    paths.append(path)

    outside = eligible[eligible["value_state"].isin(["above_value", "below_value"])]
    figure, axis = plt.subplots(figsize=(8, 5))
    horizons = [15, 30, 60, 90]
    for session, group in outside.groupby("session_name", observed=True):
        means = [
            group[f"value_fwd_{horizon}_state_aligned_return_pips"].mean()
            for horizon in horizons
        ]
        axis.plot(horizons, means, marker="o", label=session)
    axis.axhline(0, color="black", linewidth=1)
    axis.set_xlabel("Horizon (minutes)")
    axis.set_ylabel("Mean state-aligned return (pips)")
    axis.set_title("Outside-value continuation")
    axis.legend()
    axis.grid(alpha=0.25)
    figure.tight_layout()
    path = output_dir / "continuation_by_horizon.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    paths.append(path)

    transition_rows = []
    for (session, state), group in outside.groupby(
        ["session_name", "value_state"], observed=True
    ):
        accepted_column = (
            "value_fwd_60_acceptance_above"
            if state == "above_value"
            else "value_fwd_60_acceptance_below"
        )
        transition_rows.extend(
            [
                {
                    "label": f"{session}\n{state}",
                    "transition": "reentered",
                    "probability": group["value_fwd_60_reentered"].mean(),
                },
                {
                    "label": f"{session}\n{state}",
                    "transition": "accepted outside",
                    "probability": group[accepted_column].mean(),
                },
            ]
        )
    transitions = pd.DataFrame(transition_rows)
    figure, axis = plt.subplots(figsize=(10, 5))
    if not transitions.empty:
        transitions.pivot(
            index="label", columns="transition", values="probability"
        ).plot(kind="bar", ax=axis)
    axis.set_ylabel("Probability")
    axis.set_title("60-minute re-entry and acceptance")
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    path = output_dir / "reentry_acceptance.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    paths.append(path)

    figure, axis = plt.subplots(figsize=(8, 5))
    for session, group in eligible.groupby("session_name", observed=True):
        axis.scatter(
            group["vwap_distance_pips"],
            group["fwd_60_return_pips"],
            s=12,
            alpha=0.45,
            label=session,
        )
    axis.axhline(0, color="black", linewidth=1)
    axis.axvline(0, color="black", linewidth=1)
    axis.set_xlabel("Open distance to VWAP (pips)")
    axis.set_ylabel("60-minute return (pips)")
    axis.set_title("VWAP distance versus forward return")
    axis.legend()
    axis.grid(alpha=0.25)
    figure.tight_layout()
    path = output_dir / "vwap_distance_vs_return.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    paths.append(path)
    return paths


def _render_report(
    events: pd.DataFrame,
    profiles: pd.DataFrame,
    comparisons: pd.DataFrame,
    gate: dict[str, Any],
    *,
    start: str,
    end: str,
) -> str:
    eligible = events[events["value_eligible"]]
    counts = (
        eligible.groupby(["session_name", "value_state"], observed=True)
        .size()
        .reset_index(name="count")
    )
    lines = [
        "# GBPUSD Phase-2 Value-State Study",
        "",
        f"Development interval: `{start}` inclusive to `{end}` exclusive.",
        "",
        "VWAP and Volume Profile use tick activity, not centralized traded volume.",
        "This report contains no trading entries or P&L simulation.",
        "",
        "## Data quality",
        "",
        f"- Opening events: {len(events)} total; {len(eligible)} value-eligible.",
        f"- Daily profiles: {len(profiles)} total; "
        f"{int(profiles['eligible'].sum())} eligible.",
        f"- Feature coverage: {gate['feature_coverage_ratio']:.1%}.",
        f"- Development gate: **{'PASS' if gate['passed'] else 'FAIL'}**.",
        "",
        "## Opening states",
        "",
        "| Session | Value state | Events |",
        "|---|---|---:|",
    ]
    for row in counts.itertuples(index=False):
        lines.append(f"| {row.session_name} | {row.value_state} | {row.count} |")
    lines.extend(
        [
            "",
            "## Registered 60-minute contrasts",
            "",
            "| Contrast | Session | N1 | N2 | Difference | 95% CI |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    primary = comparisons[comparisons["horizon_minutes"].eq(60)]
    for row in primary.itertuples(index=False):
        lines.append(
            f"| {row.contrast} | {row.session_name} | {row.first_count} | "
            f"{row.second_count} | {row.mean_difference:.2f} | "
            f"[{row.ci_low:.2f}, {row.ci_high:.2f}] |"
        )
    lines.extend(["", "## Gate interpretation", ""])
    if gate["passed"]:
        lines.append(
            "At least one sufficiently populated value-state contrast is material, "
            "its interval excludes zero, and the coverage/spread checks pass. This "
            "supports proceeding to point-in-time fundamental-bias research."
        )
    else:
        failed = [name for name, passed in gate["checks"].items() if not passed]
        lines.append(
            "Failed checks: " + ", ".join(f"`{name}`" for name in failed) + "."
        )
        lines.append(
            "Do not add fundamental or trading-rule complexity until this result is "
            "reviewed."
        )
    lines.extend(
        [
            "",
            "A conditional movement difference is not evidence of executable "
            "profitability after spread, slippage, stops, and exits.",
            "",
        ]
    )
    return "\n".join(lines)


def run_phase2(
    project_root: Path,
    config: ProjectConfig,
    value_config: ValueStateConfig,
) -> dict[str, Any]:
    research = config.research
    bars = load_m5_range(project_root, config, research.data.start, research.data.end)
    required_moments = {"mid_activity_sum", "mid_squared_activity_sum"}
    missing = sorted(required_moments.difference(bars.columns))
    if missing:
        raise ValueError(
            "Monthly M5 files need Phase-2 moments; rerun build-range --force. "
            "Missing: " + ", ".join(missing)
        )
    enriched = enrich_fx_day_vwap(
        bars,
        config.sessions.trading_day,
        pip_size=research.instrument.pip_size,
        slope_minutes=value_config.vwap.slope_minutes,
    )
    start_utc = datetime.combine(research.data.start, datetime.min.time(), tzinfo=UTC)
    end_utc = datetime.combine(research.data.end, datetime.min.time(), tzinfo=UTC)
    calendar = build_session_calendar(start_utc, end_utc, config.sessions)
    events = build_event_dataset(
        bars,
        calendar,
        pip_size=research.instrument.pip_size,
        preopen_windows=research.study.preopen_windows_minutes,
        horizons=research.study.horizons_minutes,
        minimum_coverage_ratio=research.quality.event_min_coverage_ratio,
        event_kind="session_open",
    )
    events = attach_event_vwap(
        events,
        enriched,
        pip_size=research.instrument.pip_size,
        boundary_buffer_pips=value_config.classification.boundary_buffer_pips,
    )
    profiles = build_daily_tick_profiles(project_root, config, value_config, bars)
    events = attach_previous_profile(
        events,
        profiles,
        pip_size=research.instrument.pip_size,
        boundary_buffer_pips=value_config.classification.boundary_buffer_pips,
    )
    events = attach_value_outcomes(
        events,
        bars,
        pip_size=research.instrument.pip_size,
        boundary_buffer_pips=value_config.classification.boundary_buffer_pips,
        acceptance_consecutive_closes=(
            value_config.classification.acceptance_consecutive_closes
        ),
        horizons=value_config.classification.transition_horizons_minutes,
    )
    conditional = conditional_statistics(
        events,
        research.study.horizons_minutes,
        minimum_group_size=value_config.research_gate.minimum_group_size,
    )
    associations = continuous_feature_associations(
        events, value_config.classification.transition_horizons_minutes
    )
    comparisons = value_state_comparisons(
        events,
        horizons=value_config.classification.transition_horizons_minutes,
        resamples=research.study.bootstrap_resamples,
        confidence_level=research.study.confidence_level,
        random_seed=research.study.random_seed,
    )
    gate = _evaluate_gate(
        events,
        profiles,
        comparisons,
        value_config,
        pip_size=research.instrument.pip_size,
    )

    combined_config = {
        "project": config.model_dump(mode="json"),
        "value_state": value_config.model_dump(mode="json"),
    }
    source_snapshot = _source_snapshot(project_root, config)
    config_hash = _hash_json(combined_config)
    run_id = phase2_run_id(config, value_config)
    processed_root = resolve_within_project(project_root, research.data.paths.processed)
    output = processed_root / "reports" / "phase2" / run_id
    output.mkdir(parents=True, exist_ok=True)
    profiles.to_parquet(output / "daily_profiles.parquet", index=False)
    events.to_parquet(output / "value_events.parquet", index=False)
    events[~events["value_eligible"]].to_parquet(
        output / "event_exclusions.parquet", index=False
    )
    conditional.to_csv(output / "conditional_statistics.csv", index=False)
    associations.to_csv(output / "continuous_associations.csv", index=False)
    comparisons.to_csv(output / "statistical_comparisons.csv", index=False)
    data_quality = {
        "valid": (
            gate["checks"]["feature_coverage"]
            and gate["checks"]["eligible_daily_profiles_exist"]
            and gate["checks"]["point_in_time_invariants"]
        ),
        "gate": gate,
        "profile_eligibility": {
            "total": len(profiles),
            "eligible": int(profiles["eligible"].sum()),
        },
        "event_eligibility": {
            "total": len(events),
            "phase1_eligible": int(events["eligible"].sum()),
            "value_eligible": int(events["value_eligible"].sum()),
        },
    }
    _write_json(data_quality, output / "data_quality.json")
    figure_paths = _create_figures(events, output / "figures")
    report = _render_report(
        events,
        profiles,
        comparisons,
        gate,
        start=research.data.start.isoformat(),
        end=research.data.end.isoformat(),
    )
    (output / "report.md").write_text(report, encoding="utf-8")
    manifest = {
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "git_commit": _git_commit(project_root),
        "git_dirty": _git_dirty(project_root),
        "config": combined_config,
        "config_sha256": config_hash,
        "source_archives": source_snapshot,
        "source_snapshot_sha256": _hash_json(source_snapshot),
        "development_gate": gate,
        "runtime": {
            "python": platform.python_version(),
            "packages": {
                package: importlib.metadata.version(package)
                for package in ("numpy", "pandas", "pyarrow", "scipy", "matplotlib")
            },
        },
        "rows": {
            "m5": len(bars),
            "profiles": len(profiles),
            "events": len(events),
            "value_eligible_events": int(events["value_eligible"].sum()),
            "conditional_statistics": len(conditional),
            "continuous_associations": len(associations),
            "statistical_comparisons": len(comparisons),
        },
        "figures": [str(path.relative_to(output)) for path in figure_paths],
    }
    _write_json(manifest, output / "run_manifest.json")
    return {
        **manifest,
        "output_directory": str(output.relative_to(project_root)),
        "report": str((output / "report.md").relative_to(project_root)),
    }
