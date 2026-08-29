"""Orchestration for the Phase-3C market-implied repricing study."""

from __future__ import annotations

import importlib.metadata
import os
import platform
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from gbpusd_research.config import (
    FundamentalRepricingConfig,
    ProjectConfig,
    ValueStateConfig,
)
from gbpusd_research.data.macro import (
    load_macro_release_events,
    load_policy_decision_events,
    load_repricing_two_year_yields,
)
from gbpusd_research.features.fundamental_repricing import (
    CURRENCY_TIMEZONES,
    attach_relative_repricing_bias,
    build_catalyst_yield_shocks,
)
from gbpusd_research.research.phase2 import (
    _git_commit,
    _git_dirty,
    _hash_json,
    _write_json,
)
from gbpusd_research.research.phase3 import _load_phase2_events, _sha256_file
from gbpusd_research.utils.paths import resolve_within_project


def attach_session_day_outcomes(
    events: pd.DataFrame,
    *,
    horizons: tuple[int, ...],
    pip_size: float,
) -> pd.DataFrame:
    """Attach open-to-open outcomes at future occurrences of the same session."""

    output = events.copy()
    for horizon in horizons:
        return_column = f"repricing_fwd_{horizon}d_return_pips"
        end_column = f"repricing_fwd_{horizon}d_end_timestamp_utc"
        output[return_column] = np.nan
        output[end_column] = pd.Series(
            pd.NaT, index=output.index, dtype="datetime64[ns, UTC]"
        )
        for _, group in output.groupby("session_name", observed=True):
            ordered = group.sort_values("event_timestamp_utc", kind="stable")
            valid = ordered[ordered["open_price_mid"].notna()]
            future_price = valid["open_price_mid"].shift(-horizon)
            future_time = valid["event_timestamp_utc"].shift(-horizon)
            output.loc[valid.index, return_column] = (
                future_price - valid["open_price_mid"]
            ) / pip_size
            output.loc[valid.index, end_column] = future_time
        directional = output["repricing_bias"].ne(0)
        output[f"repricing_fwd_{horizon}d_aligned_return_pips"] = (
            output["repricing_bias"] * output[return_column]
        ).where(directional)
        reversion_direction = pd.Series(
            np.select(
                [
                    output["value_state"].eq("above_value"),
                    output["value_state"].eq("below_value"),
                ],
                [-1, 1],
                default=0,
            ),
            index=output.index,
            dtype="int8",
        )
        output[f"repricing_fwd_{horizon}d_reversion_return_pips"] = (
            reversion_direction * output[return_column]
        ).where(reversion_direction.ne(0))
    output["repricing_value_relation"] = "not_outside_value"
    reversion_direction = pd.Series(
        np.select(
            [
                output["value_state"].eq("above_value"),
                output["value_state"].eq("below_value"),
            ],
            [-1, 1],
            default=0,
        ),
        index=output.index,
        dtype="int8",
    )
    outside_directional = reversion_direction.ne(0) & output["repricing_bias"].ne(0)
    output.loc[outside_directional, "repricing_value_relation"] = np.where(
        output.loc[outside_directional, "repricing_bias"].eq(
            reversion_direction[outside_directional]
        ),
        "supports_reversion",
        "opposes_reversion",
    )
    output["repricing_eligible"] = (
        output["value_eligible"] & output["repricing_available"]
    )
    output["repricing_exclusion_reason"] = np.where(
        output["repricing_eligible"],
        None,
        np.where(
            ~output["value_eligible"],
            "phase2_ineligible",
            output["repricing_unavailable_reason"],
        ),
    )
    return output


def _cluster_bootstrap_mean(
    frame: pd.DataFrame,
    *,
    value_column: str,
    cluster_column: str,
    resamples: int,
    confidence_level: float,
    random_seed: int,
) -> tuple[float, float]:
    clusters = [
        group[value_column].dropna().to_numpy(dtype=float)
        for _, group in frame.groupby(cluster_column, observed=True)
    ]
    clusters = [values for values in clusters if len(values)]
    if not clusters:
        return np.nan, np.nan
    rng = np.random.default_rng(random_seed)
    means = np.empty(resamples)
    for index in range(resamples):
        selected = rng.integers(0, len(clusters), size=len(clusters))
        sample = np.concatenate([clusters[position] for position in selected])
        means[index] = sample.mean()
    alpha = (1 - confidence_level) / 2
    low, high = np.quantile(means, [alpha, 1 - alpha])
    return float(low), float(high)


def directional_statistics(
    events: pd.DataFrame,
    *,
    horizons: tuple[int, ...],
    resamples: int,
    confidence_level: float,
    random_seed: int,
) -> pd.DataFrame:
    """Summarize directional returns with catalyst-regime cluster intervals."""

    rows = []
    eligible = events[events["repricing_eligible"]]
    for session, session_group in eligible.groupby("session_name", observed=True):
        session_seed = sum(map(ord, str(session)))
        for horizon in horizons:
            value_column = f"repricing_fwd_{horizon}d_aligned_return_pips"
            group = session_group[
                session_group["repricing_bias"].ne(0)
                & session_group[value_column].notna()
            ]
            low, high = _cluster_bootstrap_mean(
                group,
                value_column=value_column,
                cluster_column="repricing_regime_id",
                resamples=resamples,
                confidence_level=confidence_level,
                random_seed=random_seed + session_seed + horizon * 2029,
            )
            regimes = group.groupby(
                ["repricing_regime_id", "repricing_bias_label"], observed=True
            ).size()
            rows.append(
                {
                    "session_name": session,
                    "horizon_session_days": horizon,
                    "count": len(group),
                    "unique_regimes": group["repricing_regime_id"].nunique(),
                    "long_regimes": sum(
                        label == "long" for _, label in regimes.index
                    ),
                    "short_regimes": sum(
                        label == "short" for _, label in regimes.index
                    ),
                    "mean_aligned_return_pips": group[value_column].mean(),
                    "median_aligned_return_pips": group[value_column].median(),
                    "directional_hit_rate": group[value_column].gt(0).mean(),
                    "ci_low": low,
                    "ci_high": high,
                    "confidence_level": confidence_level,
                }
            )
    return pd.DataFrame(rows)


def catalyst_statistics(
    events: pd.DataFrame, *, horizons: tuple[int, ...]
) -> pd.DataFrame:
    """Return descriptive directional results by catalyst pillar."""

    rows = []
    eligible = events[
        events["repricing_eligible"] & events["repricing_bias"].ne(0)
    ]
    for (session, pillar), group in eligible.groupby(
        ["session_name", "repricing_signal_pillar"], observed=True
    ):
        for horizon in horizons:
            column = f"repricing_fwd_{horizon}d_aligned_return_pips"
            values = group[column].dropna()
            rows.append(
                {
                    "session_name": session,
                    "signal_pillar": pillar,
                    "horizon_session_days": horizon,
                    "count": len(values),
                    "unique_regimes": group.loc[
                        values.index, "repricing_regime_id"
                    ].nunique(),
                    "mean_aligned_return_pips": values.mean(),
                    "directional_hit_rate": values.gt(0).mean(),
                }
            )
    return pd.DataFrame(rows)


def value_interactions(
    events: pd.DataFrame, *, horizons: tuple[int, ...]
) -> pd.DataFrame:
    """Describe whether the repricing bias supports outside-value reversion."""

    rows = []
    eligible = events[
        events["repricing_eligible"]
        & events["repricing_value_relation"].isin(
            ["supports_reversion", "opposes_reversion"]
        )
    ]
    for (session, relation), group in eligible.groupby(
        ["session_name", "repricing_value_relation"], observed=True
    ):
        for horizon in horizons:
            column = f"repricing_fwd_{horizon}d_reversion_return_pips"
            values = group[column].dropna()
            rows.append(
                {
                    "session_name": session,
                    "relation": relation,
                    "horizon_session_days": horizon,
                    "count": len(values),
                    "unique_regimes": group.loc[
                        values.index, "repricing_regime_id"
                    ].nunique(),
                    "mean_reversion_return_pips": values.mean(),
                }
            )
    return pd.DataFrame(rows)


def _evaluate_gate(
    events: pd.DataFrame,
    shocks: pd.DataFrame,
    statistics: pd.DataFrame,
    config: FundamentalRepricingConfig,
) -> dict[str, Any]:
    eligible = events[events["repricing_eligible"]]
    active = eligible[
        eligible["gbp_catalyst_active"] | eligible["usd_catalyst_active"]
    ]
    local_dates = pd.Series(index=shocks.index, dtype="datetime64[ns]")
    for currency, group in shocks.groupby("currency", observed=True):
        local_dates.loc[group.index] = (
            group["release_at_utc"]
            .dt.tz_convert(CURRENCY_TIMEZONES[str(currency)])
            .dt.tz_localize(None)
            .dt.normalize()
        )
    mapped = shocks[shocks["yield_mapping_available"]]
    threshold = config.signal.bias_threshold_bps
    expected_bias = pd.Series(
        np.select(
            [
                eligible["repricing_relative_shock_bps"].ge(threshold),
                eligible["repricing_relative_shock_bps"].le(-threshold),
            ],
            [1, -1],
            default=0,
        ),
        index=eligible.index,
        dtype="int8",
    )
    availability_checks = []
    age_checks = []
    for currency in ("gbp", "usd"):
        currency_active = active[active[f"{currency}_catalyst_active"]]
        availability_checks.append(
            currency_active[f"{currency}_shock_available_at"].le(
                currency_active["event_timestamp_utc"]
            ).all()
        )
        age_checks.append(
            currency_active[f"{currency}_yield_observation_age"]
            .between(0, config.signal.active_yield_observations - 1)
            .all()
        )
    invariant_checks = {
        "catalyst_ids_unique": bool(shocks["catalyst_id"].is_unique),
        "all_catalysts_mapped": bool(shocks["yield_mapping_available"].all()),
        "same_day_yield_mapping": bool(
            mapped["yield_observation_date"].eq(local_dates[mapped.index]).all()
        ),
        "preceding_yield_strictly_prior": bool(
            mapped["yield_previous_observation_date"]
            .lt(mapped["yield_observation_date"])
            .all()
        ),
        "shock_after_release_and_yield_publication": bool(
            mapped["shock_available_at_utc"].ge(mapped["release_at_utc"]).all()
            and mapped["shock_available_at_utc"]
            .ge(mapped["yield_available_at_utc"])
            .all()
        ),
        "session_signal_point_in_time": bool(all(availability_checks)),
        "active_observation_age": bool(all(age_checks)),
        "relative_shock_arithmetic": bool(
            eligible["repricing_relative_shock_bps"]
            .eq(
                eligible["gbp_yield_shock_bps"]
                - eligible["usd_yield_shock_bps"]
            )
            .all()
        ),
        "bias_threshold_mapping": bool(
            eligible["repricing_bias"].astype("int8").eq(expected_bias).all()
        ),
    }
    primary_horizon = config.analysis.primary_horizon_session_days
    primary = statistics[
        statistics["horizon_session_days"].eq(primary_horizon)
    ]
    candidates = []
    for row in primary.itertuples(index=False):
        same_session = statistics[statistics["session_name"].eq(row.session_name)]
        horizon_means = same_session.set_index("horizon_session_days")[
            "mean_aligned_return_pips"
        ].to_dict()
        enough_regimes = (
            row.unique_regimes >= config.analysis.minimum_directional_regimes
            and row.long_regimes >= config.analysis.minimum_regimes_per_direction
            and row.short_regimes >= config.analysis.minimum_regimes_per_direction
        )
        positive_horizon_consistency = all(
            horizon_means.get(horizon, np.nan) > 0
            for horizon in config.analysis.horizons_session_days
            if horizon != primary_horizon
        )
        material = (
            row.mean_aligned_return_pips >= config.analysis.materiality_pips
            and row.ci_low > 0
        )
        candidates.append(
            {
                "session_name": row.session_name,
                "enough_regimes": bool(enough_regimes),
                "positive_horizon_consistency": bool(
                    positive_horizon_consistency
                ),
                "material_primary_effect": bool(material),
                "passed": bool(
                    enough_regimes and positive_horizon_consistency and material
                ),
            }
        )
    feature_coverage = float(
        events.loc[events["value_eligible"], "repricing_available"].mean()
    )
    checks = {
        "complete_registered_catalyst_mapping": bool(
            shocks["yield_mapping_available"].all()
        ),
        "point_in_time_and_arithmetic_invariants": bool(
            all(invariant_checks.values())
        ),
        "complete_session_feature_coverage": feature_coverage == 1.0,
        "registered_primary_effect": any(
            candidate["passed"] for candidate in candidates
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "invariant_checks": invariant_checks,
        "feature_coverage_ratio": feature_coverage,
        "registered_catalysts": len(shocks),
        "mapped_catalysts": int(shocks["yield_mapping_available"].sum()),
        "session_candidates": candidates,
    }


def _create_figures(
    events: pd.DataFrame, statistics: pd.DataFrame, output_dir: Path
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = output_dir.parent / ".plot_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache_dir))
    os.environ.setdefault("XDG_CACHE_HOME", str(cache_dir))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    eligible = events[events["repricing_eligible"]]
    paths = []

    counts = (
        eligible.groupby(["session_name", "repricing_bias_label"], observed=True)
        .size()
        .unstack(fill_value=0)
    )
    figure, axis = plt.subplots(figsize=(8, 5))
    counts.plot(kind="bar", ax=axis)
    axis.set_ylabel("Session opens")
    axis.set_title("Market-implied repricing bias by session")
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
            ordered["repricing_relative_shock_bps"],
            linewidth=1,
            label=session,
        )
    axis.axhline(5, color="green", linestyle="--", linewidth=1)
    axis.axhline(-5, color="red", linestyle="--", linewidth=1)
    axis.set_ylabel("GBP minus USD event-day 2Y shock (bp)")
    axis.set_title("Point-in-time relative repricing signal")
    axis.legend()
    axis.grid(alpha=0.25)
    figure.tight_layout()
    path = output_dir / "relative_shock_timeline.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    paths.append(path)

    figure, axis = plt.subplots(figsize=(8, 5))
    for session, group in statistics.groupby("session_name", observed=True):
        axis.plot(
            group["horizon_session_days"],
            group["mean_aligned_return_pips"],
            marker="o",
            label=session,
        )
    axis.axhline(0, color="black", linewidth=1)
    axis.set_xlabel("Same-session horizon (trading days)")
    axis.set_ylabel("Bias-aligned return (pips)")
    axis.set_title("Post-repricing directional continuation")
    axis.legend()
    axis.grid(alpha=0.25)
    figure.tight_layout()
    path = output_dir / "aligned_return_by_horizon.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    paths.append(path)
    return paths


def _render_report(
    events: pd.DataFrame,
    shocks: pd.DataFrame,
    statistics: pd.DataFrame,
    gate: dict[str, Any],
    *,
    start: str,
    end: str,
) -> str:
    eligible = events[events["repricing_eligible"]]
    counts = (
        eligible.groupby(["session_name", "repricing_bias_label"], observed=True)
        .size()
        .reset_index(name="count")
    )
    lines = [
        "# GBPUSD Phase-3C Market-Implied Fundamental Surprise Study",
        "",
        f"Development interval: `{start}` inclusive to `{end}` exclusive.",
        "",
        "The frozen model uses official event-day 2Y yield repricing as a noisy ",
        "market-implied surprise proxy. It is not literal actual-minus-consensus.",
        "Outcomes start only after the official yield observation is available.",
        "This report contains no execution assumptions or P&L.",
        "",
        "## Data quality",
        "",
        f"- Registered catalysts: {len(shocks)}; mapped: {gate['mapped_catalysts']}.",
        f"- Session opens: {len(events)} total; {len(eligible)} eligible.",
        f"- Feature coverage: {gate['feature_coverage_ratio']:.1%}.",
        f"- Development gate: **{'PASS' if gate['passed'] else 'FAIL'}**.",
        "",
        "## Bias counts",
        "",
        "| Session | Bias | Opens |",
        "|---|---|---:|",
    ]
    for row in counts.itertuples(index=False):
        lines.append(
            f"| {row.session_name} | {row.repricing_bias_label} | {row.count} |"
        )
    lines.extend(
        [
            "",
            "## Registered directional results",
            "",
            "Intervals are 97.5% catalyst-regime cluster-bootstrap intervals.",
            "",
            "| Session | Horizon | N | Regimes | Long/short regimes | "
            "Mean | Hit rate | 97.5% CI |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in statistics.itertuples(index=False):
        lines.append(
            f"| {row.session_name} | {row.horizon_session_days}d | {row.count} | "
            f"{row.unique_regimes} | {row.long_regimes}/{row.short_regimes} | "
            f"{row.mean_aligned_return_pips:.2f} | "
            f"{row.directional_hit_rate:.1%} | "
            f"[{row.ci_low:.2f}, {row.ci_high:.2f}] |"
        )
    lines.extend(["", "## Gate interpretation", ""])
    if gate["passed"]:
        lines.append(
            "At least one session met the preregistered three-day materiality, "
            "family-wise interval, regime-breadth, and horizon-consistency rules. "
            "The next step is untouched-period validation, not parameter tuning."
        )
    else:
        failed = [name for name, passed in gate["checks"].items() if not passed]
        lines.append(
            "Failed checks: " + ", ".join(f"`{name}`" for name in failed) + "."
        )
        lines.append(
            "Do not tune the threshold, signal lifetime, catalyst set, or primary "
            "horizon using this 2024 outcome."
        )
    lines.extend(
        [
            "",
            "The daily yield move can contain unrelated same-day information, and "
            "UK/US official yield series use different construction methods. Any "
            "positive result would therefore remain an association requiring "
            "out-of-sample validation.",
            "",
        ]
    )
    return "\n".join(lines)


def run_phase3c(
    project_root: Path,
    config: ProjectConfig,
    value_config: ValueStateConfig,
    repricing_config: FundamentalRepricingConfig,
) -> dict[str, Any]:
    """Run the frozen event-day two-year yield repricing study."""

    phase2_events, phase2_directory, phase2_manifest = _load_phase2_events(
        project_root, config, value_config
    )
    policy_events = load_policy_decision_events(project_root, repricing_config)
    macro_events = load_macro_release_events(project_root, repricing_config)
    yields = load_repricing_two_year_yields(project_root, repricing_config)
    shocks = build_catalyst_yield_shocks(
        policy_events,
        macro_events,
        yields,
        start=config.research.data.start,
        end=config.research.data.end,
    )
    events = attach_relative_repricing_bias(
        phase2_events, shocks, yields, repricing_config
    )
    horizons = repricing_config.analysis.horizons_session_days
    events = attach_session_day_outcomes(
        events,
        horizons=horizons,
        pip_size=config.research.instrument.pip_size,
    )
    per_test_confidence = 1 - (
        1 - repricing_config.analysis.familywise_confidence_level
    ) / len(config.sessions.sessions)
    statistics = directional_statistics(
        events,
        horizons=horizons,
        resamples=repricing_config.analysis.bootstrap_resamples,
        confidence_level=per_test_confidence,
        random_seed=config.research.study.random_seed,
    )
    by_catalyst = catalyst_statistics(events, horizons=horizons)
    interactions = value_interactions(events, horizons=horizons)
    gate = _evaluate_gate(events, shocks, statistics, repricing_config)

    combined_config = {
        "project": config.model_dump(mode="json"),
        "value_state": value_config.model_dump(mode="json"),
        "fundamental_repricing": repricing_config.model_dump(mode="json"),
    }
    config_hash = _hash_json(combined_config)
    research = config.research
    run_id = (
        f"{research.data.start:%Y%m%d}_{research.data.end:%Y%m%d}_{config_hash[:8]}"
    )
    processed_root = resolve_within_project(project_root, research.data.paths.processed)
    output = processed_root / "reports" / "phase3c" / run_id
    output.mkdir(parents=True, exist_ok=True)
    shocks.to_csv(output / "catalyst_yield_shocks.csv", index=False)
    events.to_parquet(output / "session_bias.parquet", index=False)
    statistics.to_csv(output / "directional_statistics.csv", index=False)
    by_catalyst.to_csv(output / "catalyst_statistics.csv", index=False)
    interactions.to_csv(output / "value_interactions.csv", index=False)
    data_quality = {
        "valid": all(
            value
            for name, value in gate["checks"].items()
            if name != "registered_primary_effect"
        ),
        "gate": gate,
        "event_eligibility": {
            "total": len(events),
            "phase2_eligible": int(events["value_eligible"].sum()),
            "repricing_eligible": int(events["repricing_eligible"].sum()),
        },
    }
    _write_json(data_quality, output / "data_quality.json")
    figure_paths = _create_figures(events, statistics, output / "figures")
    report = _render_report(
        events,
        shocks,
        statistics,
        gate,
        start=research.data.start.isoformat(),
        end=research.data.end.isoformat(),
    )
    (output / "report.md").write_text(report, encoding="utf-8")

    ledger_paths = {
        "policy_decisions": resolve_within_project(
            project_root, repricing_config.data.policy_decisions_path
        ),
        "macro": resolve_within_project(
            project_root, repricing_config.data.macro_events_path
        ),
        "two_year_yields": resolve_within_project(
            project_root, repricing_config.data.yields_path
        ),
    }
    phase2_manifest_path = phase2_directory / "run_manifest.json"
    baseline_documents = {
        "phase3": project_root / "llm" / "PHASE3_RESULTS_2024.md",
        "phase3b": project_root / "llm" / "PHASE3B_RESULTS_2024.md",
        "phase3c_plan": project_root / "llm" / "TECHNICAL_PLAN_GBPUSD_PHASE3C.md",
    }
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
        "registered_documents": {
            name: {
                "path": str(path.relative_to(project_root)),
                "sha256": _sha256_file(path),
            }
            for name, path in baseline_documents.items()
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
            "policy_decisions": len(policy_events),
            "macro_events": len(macro_events),
            "yield_observations": len(yields),
            "catalyst_shocks": len(shocks),
            "mapped_catalyst_shocks": int(
                shocks["yield_mapping_available"].sum()
            ),
            "events": len(events),
            "repricing_eligible_events": int(events["repricing_eligible"].sum()),
            "directional_statistics": len(statistics),
            "catalyst_statistics": len(by_catalyst),
            "value_interactions": len(interactions),
        },
        "figures": [str(path.relative_to(output)) for path in figure_paths],
    }
    _write_json(manifest, output / "run_manifest.json")
    return {
        **manifest,
        "output_directory": str(output.relative_to(project_root)),
        "report": str((output / "report.md").relative_to(project_root)),
    }
