"""Orchestration and reporting for the Phase-3 policy-bias study."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from gbpusd_research.config import (
    FundamentalBiasConfig,
    ProjectConfig,
    ValueStateConfig,
)
from gbpusd_research.data.macro import load_policy_rate_events
from gbpusd_research.features.fundamentals import attach_policy_bias
from gbpusd_research.research.fundamental_bias import (
    attach_fundamental_outcomes,
    fundamental_comparisons,
    fundamental_conditional_statistics,
)
from gbpusd_research.research.phase2 import (
    _git_commit,
    _git_dirty,
    _hash_json,
    _write_json,
    phase2_run_id,
)
from gbpusd_research.utils.paths import resolve_within_project


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_phase2_events(
    project_root: Path,
    config: ProjectConfig,
    value_config: ValueStateConfig,
) -> tuple[pd.DataFrame, Path, dict[str, Any]]:
    processed_root = resolve_within_project(
        project_root, config.research.data.paths.processed
    )
    run_id = phase2_run_id(config, value_config)
    directory = processed_root / "reports" / "phase2" / run_id
    event_path = directory / "value_events.parquet"
    manifest_path = directory / "run_manifest.json"
    if not event_path.is_file() or not manifest_path.is_file():
        raise ValueError(
            "Matching Phase-2 artifacts are missing; run run-phase2 first: "
            f"{directory.relative_to(project_root)}"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("run_id") != run_id:
        raise ValueError("Phase-2 manifest run_id does not match its directory")
    if not manifest.get("development_gate", {}).get("passed", False):
        raise ValueError("Matching Phase-2 development gate did not pass")
    events = pd.read_parquet(event_path)
    return events, directory, manifest


def _evaluate_gate(
    events: pd.DataFrame,
    policy_events: pd.DataFrame,
    comparisons: pd.DataFrame,
    config: FundamentalBiasConfig,
) -> dict[str, Any]:
    base = events[events["value_eligible"]]
    coverage = float(base["fundamental_eligible"].mean())
    eligible = events[events["fundamental_eligible"]]
    lookback_delta = pd.Timedelta(config.policy.impulse_lookback_days, unit="D")
    score_sign = np.sign(eligible["policy_relative_score"]).astype("int8")
    invariant_checks = {
        "policy_event_ids_unique": bool(policy_events["event_id"].is_unique),
        "current_gbp_available_at_event": bool(
            (
                eligible["gbp_policy_available_at"] <= eligible["event_timestamp_utc"]
            ).all()
        ),
        "current_usd_available_at_event": bool(
            (
                eligible["usd_policy_available_at"] <= eligible["event_timestamp_utc"]
            ).all()
        ),
        "gbp_lookback_available_at_cutoff": bool(
            (
                eligible["gbp_lookback_available_at"]
                <= eligible["event_timestamp_utc"] - lookback_delta
            ).all()
        ),
        "usd_lookback_available_at_cutoff": bool(
            (
                eligible["usd_lookback_available_at"]
                <= eligible["event_timestamp_utc"] - lookback_delta
            ).all()
        ),
        "relative_score_arithmetic": bool(
            eligible["policy_relative_score"]
            .eq(eligible["policy_carry_signal"] + eligible["policy_impulse_signal"])
            .all()
        ),
        "bias_sign_mapping": bool(
            eligible["fundamental_bias"].astype("int8").eq(score_sign).all()
        ),
    }
    direction_months = (
        eligible[eligible["fundamental_bias_label"].isin(["long", "short"])]
        .groupby("fundamental_bias_label", observed=True)["calendar_month"]
        .nunique()
        .to_dict()
    )
    direction_breadth = all(
        direction_months.get(direction, 0) >= config.analysis.minimum_direction_months
        for direction in ("long", "short")
    )
    minimum = config.analysis.minimum_group_size
    sized = comparisons[
        comparisons["first_count"].ge(minimum)
        & (comparisons["second_count"].eq(0) | comparisons["second_count"].ge(minimum))
    ]
    material = sized[
        sized["mean_difference"].abs().ge(config.analysis.materiality_pips)
        & ((sized["ci_low"] > 0) | (sized["ci_high"] < 0))
    ]
    checks = {
        "feature_coverage": coverage >= config.analysis.minimum_feature_coverage_ratio,
        "point_in_time_invariants": all(invariant_checks.values()),
        "minimum_primary_group_size": not sized.empty,
        "direction_month_breadth": direction_breadth,
        "material_fundamental_contrast": not material.empty,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "invariant_checks": invariant_checks,
        "feature_coverage_ratio": coverage,
        "direction_months": direction_months,
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
    }


def _create_figures(
    events: pd.DataFrame, policy_events: pd.DataFrame, output_dir: Path
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = output_dir.parent / ".plot_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache_dir))
    os.environ.setdefault("XDG_CACHE_HOME", str(cache_dir))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    eligible = events[events["fundamental_eligible"]]
    paths = []

    counts = (
        eligible.groupby(["session_name", "fundamental_bias_label"], observed=True)
        .size()
        .unstack(fill_value=0)
    )
    figure, axis = plt.subplots(figsize=(8, 5))
    counts.plot(kind="bar", ax=axis)
    axis.set_ylabel("Events")
    axis.set_title("Policy bias by session")
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    path = output_dir / "bias_counts.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    paths.append(path)

    horizons = [15, 30, 60, 90]
    figure, axis = plt.subplots(figsize=(8, 5))
    for session, group in eligible.groupby("session_name", observed=True):
        means = [
            group[f"fundamental_fwd_{horizon}_bias_aligned_return_pips"].mean()
            for horizon in horizons
        ]
        axis.plot(horizons, means, marker="o", label=session)
    axis.axhline(0, color="black", linewidth=1)
    axis.set_xlabel("Horizon (minutes)")
    axis.set_ylabel("Bias-aligned return (pips)")
    axis.set_title("Policy-bias directional alignment")
    axis.legend()
    axis.grid(alpha=0.25)
    figure.tight_layout()
    path = output_dir / "aligned_return_by_horizon.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    paths.append(path)

    outside = eligible[
        eligible["policy_value_relation"].isin(
            ["supports_reversion", "opposes_reversion"]
        )
    ]
    interaction = (
        outside.groupby(["session_name", "policy_value_relation"], observed=True)[
            "fundamental_fwd_60_reversion_aligned_return_pips"
        ]
        .mean()
        .unstack()
    )
    figure, axis = plt.subplots(figsize=(9, 5))
    interaction.plot(kind="bar", ax=axis)
    axis.axhline(0, color="black", linewidth=1)
    axis.set_ylabel("60-minute reversion-aligned return (pips)")
    axis.set_title("Policy bias and outside-value reversion")
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    path = output_dir / "value_reversion_interaction.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    paths.append(path)

    figure, axis = plt.subplots(figsize=(10, 5))
    for currency, group in policy_events.groupby("currency", observed=True):
        axis.step(
            group["available_at_utc"],
            group["rate_mid_pct"],
            where="post",
            label=currency,
        )
    axis.set_ylabel("Policy rate / target midpoint (%)")
    axis.set_title("Official policy-event timeline")
    axis.legend()
    axis.grid(alpha=0.25)
    figure.tight_layout()
    path = output_dir / "policy_timeline.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    paths.append(path)
    return paths


def _render_report(
    events: pd.DataFrame,
    comparisons: pd.DataFrame,
    gate: dict[str, Any],
    *,
    start: str,
    end: str,
) -> str:
    eligible = events[events["fundamental_eligible"]]
    counts = (
        eligible.groupby(["session_name", "fundamental_bias_label"], observed=True)
        .size()
        .reset_index(name="count")
    )
    lines = [
        "# GBPUSD Phase-3 Fundamental Policy-Bias Study",
        "",
        f"Development interval: `{start}` inclusive to `{end}` exclusive.",
        "",
        "The frozen `policy_bias_v1` uses only official BoE/Fed rate decisions.",
        "This report contains no entries, execution assumptions, or P&L.",
        "",
        "## Data quality",
        "",
        f"- Opening events: {len(events)} total; {len(eligible)} fundamental-eligible.",
        f"- Feature coverage: {gate['feature_coverage_ratio']:.1%}.",
        f"- Development gate: **{'PASS' if gate['passed'] else 'FAIL'}**.",
        "",
        "## Bias counts",
        "",
        "| Session | Bias | Events |",
        "|---|---|---:|",
    ]
    for row in counts.itertuples(index=False):
        lines.append(
            f"| {row.session_name} | {row.fundamental_bias_label} | {row.count} |"
        )
    lines.extend(
        [
            "",
            "## Registered 60-minute contrasts",
            "",
            "| Contrast | Session | N1 | N2 | Difference | 95% CI |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in comparisons[comparisons["horizon_minutes"].eq(60)].itertuples(
        index=False
    ):
        lines.append(
            f"| {row.contrast} | {row.session_name} | {row.first_count} | "
            f"{row.second_count} | {row.mean_difference:.2f} | "
            f"[{row.ci_low:.2f}, {row.ci_high:.2f}] |"
        )
    lines.extend(["", "## Gate interpretation", ""])
    if gate["passed"]:
        lines.append(
            "At least one sufficiently populated fundamental contrast is material, "
            "its interval excludes zero, and all coverage/breadth/invariant checks "
            "pass. This supports technical-setup construction."
        )
    else:
        failed = [name for name, passed in gate["checks"].items() if not passed]
        lines.append(
            "Failed checks: " + ", ".join(f"`{name}`" for name in failed) + "."
        )
        lines.append(
            "Do not tune this score on 2024 or proceed to technical entries without "
            "reviewing the failed evidence."
        )
    lines.extend(
        [
            "",
            "A policy-rate association is not evidence of executable profitability. "
            "This V1 score deliberately excludes revision-prone macro histories.",
            "",
        ]
    )
    return "\n".join(lines)


def run_phase3(
    project_root: Path,
    config: ProjectConfig,
    value_config: ValueStateConfig,
    fundamental_config: FundamentalBiasConfig,
) -> dict[str, Any]:
    """Run the frozen policy-bias study against matching Phase-2 events."""

    phase2_events, phase2_directory, phase2_manifest = _load_phase2_events(
        project_root, config, value_config
    )
    policy_events = load_policy_rate_events(project_root, fundamental_config)
    events = attach_policy_bias(
        phase2_events,
        policy_events,
        impulse_lookback_days=fundamental_config.policy.impulse_lookback_days,
    )
    horizons = fundamental_config.analysis.horizons_minutes
    events = attach_fundamental_outcomes(events, horizons)
    conditional = fundamental_conditional_statistics(
        events,
        horizons,
        minimum_group_size=fundamental_config.analysis.minimum_group_size,
    )
    comparisons = fundamental_comparisons(
        events,
        horizons=horizons,
        resamples=config.research.study.bootstrap_resamples,
        confidence_level=config.research.study.confidence_level,
        random_seed=config.research.study.random_seed,
    )
    gate = _evaluate_gate(events, policy_events, comparisons, fundamental_config)

    combined_config = {
        "project": config.model_dump(mode="json"),
        "value_state": value_config.model_dump(mode="json"),
        "fundamental_bias": fundamental_config.model_dump(mode="json"),
    }
    config_hash = _hash_json(combined_config)
    research = config.research
    run_id = (
        f"{research.data.start:%Y%m%d}_{research.data.end:%Y%m%d}_{config_hash[:8]}"
    )
    processed_root = resolve_within_project(project_root, research.data.paths.processed)
    output = processed_root / "reports" / "phase3" / run_id
    output.mkdir(parents=True, exist_ok=True)
    policy_events.to_csv(output / "policy_timeline.csv", index=False)
    events.to_parquet(output / "fundamental_events.parquet", index=False)
    events[~events["fundamental_eligible"]].to_parquet(
        output / "event_exclusions.parquet", index=False
    )
    conditional.to_csv(output / "conditional_statistics.csv", index=False)
    comparisons.to_csv(output / "statistical_comparisons.csv", index=False)
    data_quality = {
        "valid": (
            gate["checks"]["feature_coverage"]
            and gate["checks"]["point_in_time_invariants"]
            and gate["checks"]["direction_month_breadth"]
        ),
        "gate": gate,
        "event_eligibility": {
            "total": len(events),
            "phase2_eligible": int(events["value_eligible"].sum()),
            "fundamental_eligible": int(events["fundamental_eligible"].sum()),
        },
    }
    _write_json(data_quality, output / "data_quality.json")
    figure_paths = _create_figures(events, policy_events, output / "figures")
    report = _render_report(
        events,
        comparisons,
        gate,
        start=research.data.start.isoformat(),
        end=research.data.end.isoformat(),
    )
    (output / "report.md").write_text(report, encoding="utf-8")

    policy_path = resolve_within_project(
        project_root, fundamental_config.policy.events_path
    )
    phase2_manifest_path = phase2_directory / "run_manifest.json"
    manifest = {
        "run_id": run_id,
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "git_commit": _git_commit(project_root),
        "git_dirty": _git_dirty(project_root),
        "config": combined_config,
        "config_sha256": config_hash,
        "policy_ledger": {
            "path": str(policy_path.relative_to(project_root)),
            "sha256": _sha256_file(policy_path),
            "events": len(policy_events),
        },
        "phase2_input": {
            "run_id": phase2_manifest["run_id"],
            "directory": str(phase2_directory.relative_to(project_root)),
            "manifest_sha256": _sha256_file(phase2_manifest_path),
        },
        "development_gate": gate,
        "runtime": {
            "python": platform.python_version(),
            "packages": {
                package: importlib.metadata.version(package)
                for package in ("numpy", "pandas", "pyarrow", "matplotlib")
            },
        },
        "rows": {
            "policy_events": len(policy_events),
            "events": len(events),
            "fundamental_eligible_events": int(events["fundamental_eligible"].sum()),
            "conditional_statistics": len(conditional),
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
