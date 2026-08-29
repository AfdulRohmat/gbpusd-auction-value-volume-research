"""Orchestration for the Phase-3B relative fundamental-strength study."""

from __future__ import annotations

import importlib.metadata
import os
import platform
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from gbpusd_research.config import (
    FundamentalStrengthConfig,
    ProjectConfig,
    ValueStateConfig,
)
from gbpusd_research.data.macro import (
    load_macro_release_events,
    load_strength_policy_rate_events,
    load_two_year_yields,
)
from gbpusd_research.features.fundamental_strength import (
    attach_relative_fundamental_strength,
)
from gbpusd_research.features.fundamentals import attach_policy_bias
from gbpusd_research.research.fundamental_bias import (
    _one_sample_bootstrap,
    attach_fundamental_outcomes,
    fundamental_comparisons,
    fundamental_conditional_statistics,
)
from gbpusd_research.research.phase2 import (
    _git_commit,
    _git_dirty,
    _hash_json,
    _write_json,
)
from gbpusd_research.research.phase3 import _load_phase2_events, _sha256_file
from gbpusd_research.utils.paths import resolve_within_project


def _threshold_bias(score: pd.Series, threshold: int) -> pd.Series:
    return pd.Series(
        np.select([score.ge(threshold), score.le(-threshold)], [1, -1], default=0),
        index=score.index,
        dtype="int8",
    )


def _attach_baseline_and_secondary_outcomes(
    events: pd.DataFrame,
    phase2_events: pd.DataFrame,
    policy_events: pd.DataFrame,
    horizons: tuple[int, ...],
) -> pd.DataFrame:
    baseline = attach_policy_bias(
        phase2_events, policy_events, impulse_lookback_days=90
    )
    output = events.copy()
    output["policy_v1_bias"] = baseline["fundamental_bias"]
    output["policy_v1_bias_label"] = baseline["fundamental_bias_label"]
    output["policy_v1_relative_score"] = baseline["policy_relative_score"]
    for horizon in horizons:
        returns = output[f"fwd_{horizon}_return_pips"]
        output[f"weighted_fwd_{horizon}_bias_aligned_return_pips"] = (
            output["weighted_fundamental_bias"] * returns
        ).where(output["weighted_fundamental_bias"].ne(0))
        output[f"policy_v1_fwd_{horizon}_bias_aligned_return_pips"] = (
            output["policy_v1_bias"] * returns
        ).where(output["policy_v1_bias"].ne(0))
        both_directional = output["fundamental_bias"].ne(0) & output[
            "policy_v1_bias"
        ].ne(0)
        output[f"fwd_{horizon}_primary_minus_policy_v1_pips"] = (
            output["fundamental_bias"] * returns - output["policy_v1_bias"] * returns
        ).where(both_directional)
    return output


def _incremental_comparisons(
    events: pd.DataFrame,
    *,
    horizons: tuple[int, ...],
    resamples: int,
    confidence_level: float,
    random_seed: int,
) -> pd.DataFrame:
    alpha = (1 - confidence_level) / 2
    rows = []
    eligible = events[events["fundamental_eligible"]]
    for session, group in eligible.groupby("session_name", observed=True):
        session_seed = sum(map(ord, str(session)))
        for horizon in horizons:
            values = (
                group[f"fwd_{horizon}_primary_minus_policy_v1_pips"]
                .dropna()
                .to_numpy()
            )
            if not len(values):
                continue
            mean, low, high = _one_sample_bootstrap(
                values,
                resamples=resamples,
                alpha=alpha,
                rng=np.random.default_rng(
                    random_seed + session_seed + horizon * 2029
                ),
            )
            rows.append(
                {
                    "contrast": "primary_minus_policy_v1_aligned_return",
                    "session_name": session,
                    "horizon_minutes": horizon,
                    "first_count": len(values),
                    "second_count": 0,
                    "first_mean": float(values.mean()),
                    "second_mean": 0.0,
                    "mean_difference": mean,
                    "ci_low": low,
                    "ci_high": high,
                }
            )
    return pd.DataFrame(rows)


def _sensitivity_statistics(
    events: pd.DataFrame, horizons: tuple[int, ...]
) -> pd.DataFrame:
    eligible = events[events["fundamental_eligible"]]
    rows = []
    for session, group in eligible.groupby("session_name", observed=True):
        for agreement, subset in group.groupby("weighting_agreement", observed=True):
            rows.append(
                {
                    "statistic": "weighting_agreement_count",
                    "session_name": session,
                    "category": agreement,
                    "horizon_minutes": np.nan,
                    "count": len(subset),
                    "mean_pips": np.nan,
                }
            )
        for horizon in horizons:
            values = group[
                f"weighted_fwd_{horizon}_bias_aligned_return_pips"
            ].dropna()
            rows.append(
                {
                    "statistic": "weighted_bias_aligned_return",
                    "session_name": session,
                    "category": "directional",
                    "horizon_minutes": horizon,
                    "count": len(values),
                    "mean_pips": values.mean() if len(values) else np.nan,
                }
            )
    return pd.DataFrame(rows)


def _evaluate_gate(
    events: pd.DataFrame,
    policy_events: pd.DataFrame,
    macro_events: pd.DataFrame,
    yields: pd.DataFrame,
    comparisons: pd.DataFrame,
    config: FundamentalStrengthConfig,
) -> dict[str, Any]:
    base = events[events["value_eligible"]]
    coverage = float(base["fundamental_eligible"].mean())
    eligible = events[events["fundamental_eligible"]]
    weights = config.scoring.robustness_weights

    availability_columns = [
        column
        for column in eligible.columns
        if column.endswith("_available_at")
    ]
    availability_valid = all(
        eligible[column].le(eligible["event_timestamp_utc"]).all()
        for column in availability_columns
    )
    yield_dates_valid = all(
        (
            eligible[f"{currency}_yield_observation_date"]
            < eligible[f"{currency}_yield_cutoff_date"]
        ).all()
        and (
            eligible[f"{currency}_yield_lookback_date"]
            < eligible[f"{currency}_yield_observation_date"]
        ).all()
        for currency in ("gbp", "usd")
    )
    equal_scores_valid = all(
        eligible[f"{currency}_score"]
        .eq(
            eligible[f"{currency}_policy_score"]
            + eligible[f"{currency}_inflation_score"]
            + eligible[f"{currency}_labor_score"]
            + eligible[f"{currency}_yield_expectation_score"]
        )
        .all()
        for currency in ("gbp", "usd")
    )
    weighted_scores_valid = all(
        eligible[f"{currency}_weighted_score"]
        .eq(
            weights.policy * eligible[f"{currency}_policy_score"]
            + weights.inflation * eligible[f"{currency}_inflation_score"]
            + weights.labor * eligible[f"{currency}_labor_score"]
            + weights.yield_expectation
            * eligible[f"{currency}_yield_expectation_score"]
        )
        .all()
        for currency in ("gbp", "usd")
    )
    relative_valid = eligible["fundamental_relative_score"].eq(
        eligible["gbp_score"] - eligible["usd_score"]
    ).all()
    weighted_relative_valid = eligible[
        "weighted_fundamental_relative_score"
    ].eq(eligible["gbp_weighted_score"] - eligible["usd_weighted_score"]).all()
    bias_valid = eligible["fundamental_bias"].astype("int8").eq(
        _threshold_bias(
            eligible["fundamental_relative_score"],
            config.scoring.primary_bias_threshold,
        )
    ).all()
    weighted_bias_valid = eligible["weighted_fundamental_bias"].astype("int8").eq(
        _threshold_bias(
            eligible["weighted_fundamental_relative_score"],
            config.scoring.weighted_bias_threshold,
        )
    ).all()
    invariant_checks = {
        "policy_event_ids_unique": bool(policy_events["event_id"].is_unique),
        "macro_event_ids_unique": bool(macro_events["event_id"].is_unique),
        "yield_currency_dates_unique": bool(
            ~yields.duplicated(["currency", "observation_date"]).any()
        ),
        "release_availability_at_event": availability_valid,
        "prior_close_yield_dates": yield_dates_valid,
        "equal_weight_score_arithmetic": equal_scores_valid,
        "weighted_score_arithmetic": weighted_scores_valid,
        "relative_score_arithmetic": bool(relative_valid),
        "weighted_relative_score_arithmetic": bool(weighted_relative_valid),
        "primary_bias_threshold_mapping": bool(bias_valid),
        "weighted_bias_threshold_mapping": bool(weighted_bias_valid),
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
    direction_counts = (
        eligible[eligible["fundamental_bias_label"].isin(["long", "short"])]
        .groupby(["session_name", "fundamental_bias_label"], observed=True)
        .size()
        .to_dict()
    )
    minimum = config.analysis.minimum_group_size
    direction_group_size = all(
        max(
            (
                count
                for (session, label), count in direction_counts.items()
                if label == direction
            ),
            default=0,
        )
        >= minimum
        for direction in ("long", "short")
    )
    primary_names = {
        "bias_aligned_return_vs_zero",
        "supports_minus_opposes_value_reversion",
    }
    primary = comparisons[comparisons["contrast"].isin(primary_names)]
    sized = primary[
        primary["first_count"].ge(minimum)
        & (primary["second_count"].eq(0) | primary["second_count"].ge(minimum))
    ]
    material = sized[
        sized["mean_difference"].abs().ge(config.analysis.materiality_pips)
        & ((sized["ci_low"] > 0) | (sized["ci_high"] < 0))
    ]
    checks = {
        "feature_coverage": coverage >= config.analysis.minimum_feature_coverage_ratio,
        "point_in_time_invariants": all(invariant_checks.values()),
        "minimum_primary_group_size": not sized.empty,
        "minimum_direction_group_size": direction_group_size,
        "direction_month_breadth": direction_breadth,
        "material_primary_contrast": not material.empty,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "invariant_checks": invariant_checks,
        "feature_coverage_ratio": coverage,
        "direction_months": direction_months,
        "direction_counts": {
            f"{session}:{label}": count
            for (session, label), count in direction_counts.items()
        },
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


def _create_figures(events: pd.DataFrame, output_dir: Path) -> list[Path]:
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
    axis.set_title("Equal-weight relative bias by session")
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    path = output_dir / "bias_counts.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    paths.append(path)

    figure, axis = plt.subplots(figsize=(10, 5))
    for session, group in eligible.groupby("session_name", observed=True):
        ordered = group.sort_values("event_timestamp_utc")
        axis.plot(
            ordered["event_timestamp_utc"],
            ordered["fundamental_relative_score"],
            linewidth=1,
            label=session,
        )
    axis.axhline(2, color="green", linestyle="--", linewidth=1)
    axis.axhline(-2, color="red", linestyle="--", linewidth=1)
    axis.set_ylabel("Relative score")
    axis.set_title("Point-in-time GBP score minus USD score")
    axis.legend()
    axis.grid(alpha=0.25)
    figure.tight_layout()
    path = output_dir / "relative_score_timeline.png"
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
    axis.set_title("Equal-weight fundamental directional alignment")
    axis.legend()
    axis.grid(alpha=0.25)
    figure.tight_layout()
    path = output_dir / "aligned_return_by_horizon.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    paths.append(path)

    agreement = (
        eligible.groupby(["session_name", "weighting_agreement"], observed=True)
        .size()
        .unstack(fill_value=0)
    )
    figure, axis = plt.subplots(figsize=(8, 5))
    agreement.plot(kind="bar", ax=axis)
    axis.set_ylabel("Events")
    axis.set_title("Equal-weight versus impact-weighted direction")
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    path = output_dir / "weighting_agreement.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    paths.append(path)

    outside = eligible[
        eligible["fundamental_value_relation"].isin(
            ["supports_reversion", "opposes_reversion"]
        )
    ]
    interaction = (
        outside.groupby(
            ["session_name", "fundamental_value_relation"], observed=True
        )["fundamental_fwd_60_reversion_aligned_return_pips"]
        .mean()
        .unstack()
    )
    figure, axis = plt.subplots(figsize=(9, 5))
    interaction.plot(kind="bar", ax=axis)
    axis.axhline(0, color="black", linewidth=1)
    axis.set_ylabel("60-minute reversion-aligned return (pips)")
    axis.set_title("Relative bias and outside-value reversion")
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    path = output_dir / "value_reversion_interaction.png"
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
    agreement = eligible["weighting_agreement"].value_counts().to_dict()
    lines = [
        "# GBPUSD Phase-3B Relative Fundamental-Strength Study",
        "",
        f"Development interval: `{start}` inclusive to `{end}` exclusive.",
        "",
        "The primary model independently scores GBP and USD policy, inflation, ",
        "earnings, and prior-close two-year yield momentum with equal weights.",
        "The 3-2-2-1 impact weighting is sensitivity-only and cannot pass the gate.",
        "This report contains no entries, execution assumptions, or P&L.",
        "",
        "## Data quality",
        "",
        f"- Opening events: {len(events)} total; {len(eligible)} fundamental-eligible.",
        f"- Complete-feature coverage: {gate['feature_coverage_ratio']:.1%}.",
        f"- Development gate: **{'PASS' if gate['passed'] else 'FAIL'}**.",
        "",
        "## Primary bias counts",
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
    lines.extend(
        [
            "",
            "## Weighting sensitivity",
            "",
            f"- Same primary/weighted direction: {agreement.get('agree', 0)} events.",
            "- One model neutral and the other directional: "
            f"{agreement.get('neutral_mismatch', 0)} events.",
            f"- Opposite directions: {agreement.get('disagree', 0)} events.",
            "",
            "## Gate interpretation",
            "",
        ]
    )
    if gate["passed"]:
        lines.append(
            "At least one sufficiently populated equal-weight contrast is material, "
            "its interval excludes zero, and all data checks pass. This supports only "
            "the next technical-setup research step."
        )
    else:
        failed = [name for name, passed in gate["checks"].items() if not passed]
        lines.append(
            "Failed checks: " + ", ".join(f"`{name}`" for name in failed) + "."
        )
        lines.append(
            "Do not change weights, thresholds, yield lookback, or pillar rules using "
            "this 2024 outcome."
        )
    lines.extend(
        [
            "",
            "Archived release values avoid later-vintage substitution. UK and US "
            "unemployment/payroll quantities are excluded from the labor pillar so "
            "the 2024 UK methodology transition cannot create a false relative signal.",
            "",
        ]
    )
    return "\n".join(lines)


def run_phase3b(
    project_root: Path,
    config: ProjectConfig,
    value_config: ValueStateConfig,
    fundamental_config: FundamentalStrengthConfig,
) -> dict[str, Any]:
    """Run the frozen relative-strength study against matching Phase-2 events."""

    phase2_events, phase2_directory, phase2_manifest = _load_phase2_events(
        project_root, config, value_config
    )
    policy_events = load_strength_policy_rate_events(project_root, fundamental_config)
    macro_events = load_macro_release_events(project_root, fundamental_config)
    yields = load_two_year_yields(project_root, fundamental_config)
    events = attach_relative_fundamental_strength(
        phase2_events,
        policy_events,
        macro_events,
        yields,
        fundamental_config,
    )
    horizons = fundamental_config.analysis.horizons_minutes
    events = attach_fundamental_outcomes(
        events,
        horizons,
        relation_column="fundamental_value_relation",
        unavailable_reason="incomplete_relative_strength_history",
    )
    events = _attach_baseline_and_secondary_outcomes(
        events, phase2_events, policy_events, horizons
    )
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
        relation_column="fundamental_value_relation",
    )
    incremental = _incremental_comparisons(
        events,
        horizons=horizons,
        resamples=config.research.study.bootstrap_resamples,
        confidence_level=config.research.study.confidence_level,
        random_seed=config.research.study.random_seed,
    )
    if not incremental.empty:
        comparisons = pd.concat([comparisons, incremental], ignore_index=True)
    sensitivity = _sensitivity_statistics(events, horizons)
    gate = _evaluate_gate(
        events,
        policy_events,
        macro_events,
        yields,
        comparisons,
        fundamental_config,
    )

    combined_config = {
        "project": config.model_dump(mode="json"),
        "value_state": value_config.model_dump(mode="json"),
        "fundamental_strength": fundamental_config.model_dump(mode="json"),
    }
    config_hash = _hash_json(combined_config)
    research = config.research
    run_id = (
        f"{research.data.start:%Y%m%d}_{research.data.end:%Y%m%d}_{config_hash[:8]}"
    )
    processed_root = resolve_within_project(project_root, research.data.paths.processed)
    output = processed_root / "reports" / "phase3b" / run_id
    output.mkdir(parents=True, exist_ok=True)
    macro_events.to_csv(output / "macro_timeline.csv", index=False)
    yields.to_csv(output / "yield_timeline.csv", index=False)
    events.to_parquet(output / "session_bias.parquet", index=False)
    events[~events["fundamental_eligible"]].to_parquet(
        output / "event_exclusions.parquet", index=False
    )
    conditional.to_csv(output / "conditional_statistics.csv", index=False)
    comparisons.to_csv(output / "statistical_comparisons.csv", index=False)
    sensitivity.to_csv(output / "sensitivity_statistics.csv", index=False)
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
    figure_paths = _create_figures(events, output / "figures")
    report = _render_report(
        events,
        comparisons,
        gate,
        start=research.data.start.isoformat(),
        end=research.data.end.isoformat(),
    )
    (output / "report.md").write_text(report, encoding="utf-8")

    ledger_paths = {
        "policy": resolve_within_project(
            project_root, fundamental_config.data.policy_events_path
        ),
        "macro": resolve_within_project(
            project_root, fundamental_config.data.macro_events_path
        ),
        "two_year_yields": resolve_within_project(
            project_root, fundamental_config.data.yields_path
        ),
    }
    phase2_manifest_path = phase2_directory / "run_manifest.json"
    phase3_v1_result = project_root / "llm" / "PHASE3_RESULTS_2024.md"
    manifest = {
        "run_id": run_id,
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "git_commit": _git_commit(project_root),
        "git_dirty": _git_dirty(project_root),
        "config": combined_config,
        "config_sha256": config_hash,
        "ledgers": {
            name: {
                "path": str(path.relative_to(project_root)),
                "sha256": _sha256_file(path),
            }
            for name, path in ledger_paths.items()
        },
        "phase2_input": {
            "run_id": phase2_manifest["run_id"],
            "directory": str(phase2_directory.relative_to(project_root)),
            "manifest_sha256": _sha256_file(phase2_manifest_path),
        },
        "phase3_v1_baseline": {
            "result_document": str(phase3_v1_result.relative_to(project_root)),
            "result_document_sha256": _sha256_file(phase3_v1_result),
            "policy_impulse_lookback_days": 90,
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
            "macro_events": len(macro_events),
            "yield_observations": len(yields),
            "events": len(events),
            "fundamental_eligible_events": int(events["fundamental_eligible"].sum()),
            "conditional_statistics": len(conditional),
            "statistical_comparisons": len(comparisons),
            "sensitivity_statistics": len(sensitivity),
        },
        "figures": [str(path.relative_to(output)) for path in figure_paths],
    }
    _write_json(manifest, output / "run_manifest.json")
    return {
        **manifest,
        "output_directory": str(output.relative_to(project_root)),
        "report": str((output / "report.md").relative_to(project_root)),
    }
