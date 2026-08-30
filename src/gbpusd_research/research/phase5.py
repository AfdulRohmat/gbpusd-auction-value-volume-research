"""Orchestration for Phase-5 opening-auction component ablation."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from gbpusd_research.config import (
    OpeningAblationConfig,
    OpeningValueStrategyConfig,
    ProjectConfig,
    ValueStateConfig,
)
from gbpusd_research.data.pipeline import load_m5_range
from gbpusd_research.research.opening_ablation import simulate_opening_ablation
from gbpusd_research.research.phase4 import (
    _artifact_records,
    _git_state,
    _load_upstream,
)
from gbpusd_research.utils.paths import resolve_within_project

PAIRED_CONTRASTS = (
    (
        "confirmation_delay",
        "signal_cohort_open_timeout_90",
        "confirmed_timeout_all",
    ),
    (
        "poc_target",
        "confirmed_timeout_favorable",
        "confirmed_poc_no_stop",
    ),
    ("excursion_stop", "confirmed_poc_no_stop", "phase4_full"),
)


def _hash_json(content: object) -> str:
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, default=str).encode()
    ).hexdigest()


def _write_json(content: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(content, indent=2, sort_keys=True), encoding="utf-8")


def phase5_run_id(
    first: ProjectConfig,
    second: ProjectConfig,
    value_config: ValueStateConfig,
    strategy_config: OpeningValueStrategyConfig,
    ablation_config: OpeningAblationConfig,
) -> str:
    content = {
        "first": first.model_dump(mode="json"),
        "second": second.model_dump(mode="json"),
        "value_state": value_config.model_dump(mode="json"),
        "opening_value": strategy_config.model_dump(mode="json"),
        "ablation": ablation_config.model_dump(mode="json"),
    }
    return (
        f"{first.research.data.start:%Y%m%d}_"
        f"{second.research.data.end:%Y%m%d}_{_hash_json(content)[:8]}"
    )


def _cluster_interval(
    frame: pd.DataFrame,
    column: str,
    *,
    resamples: int,
    confidence_level: float,
    random_seed: int,
) -> tuple[float, float]:
    if frame.empty:
        return np.nan, np.nan
    groups = [
        group[column].to_numpy(dtype=float)
        for _, group in frame.groupby("entry_month", observed=True, sort=True)
    ]
    rng = np.random.default_rng(random_seed)
    values = np.empty(resamples)
    for index in range(resamples):
        choices = rng.integers(0, len(groups), size=len(groups))
        values[index] = np.concatenate([groups[item] for item in choices]).mean()
    alpha = (1 - confidence_level) / 2
    low, high = np.quantile(values, [alpha, 1 - alpha])
    return float(low), float(high)


def _maximum_drawdown(values: pd.Series) -> float:
    equity = np.concatenate(([0.0], values.cumsum().to_numpy(dtype=float)))
    return float(np.max(np.maximum.accumulate(equity) - equity))


def variant_statistics(
    results: pd.DataFrame,
    populations: pd.DataFrame,
    config: OpeningAblationConfig,
    *,
    random_seed: int,
) -> pd.DataFrame:
    rows = []
    population_lookup = populations.set_index(["sample_year", "session_name"])
    grouped = results.groupby(
        ["sample_year", "session_name", "variant"], observed=True, sort=True
    )
    for group_index, ((year, session, variant), frame) in enumerate(grouped):
        frame = frame.sort_values("entry_timestamp_utc", kind="stable").copy()
        frame["entry_month"] = pd.to_datetime(frame["local_session_date"]).dt.to_period(
            "M"
        ).astype(str)
        positive = frame.loc[frame["pnl_pips"] > 0, "pnl_pips"].sum()
        negative = -frame.loc[frame["pnl_pips"] < 0, "pnl_pips"].sum()
        profit_factor = (
            float(positive / negative)
            if negative > 0
            else (np.inf if positive > 0 else np.nan)
        )
        low, high = _cluster_interval(
            frame,
            "pnl_pips",
            resamples=config.analysis.bootstrap_resamples,
            confidence_level=config.analysis.confidence_level,
            random_seed=random_seed + group_index,
        )
        population = population_lookup.loc[(year, session)]
        rows.append(
            {
                "sample_year": year,
                "session_name": session,
                "variant": variant,
                "scheduled_events": int(population["scheduled_events"]),
                "outside_candidates": int(population["outside_candidates"]),
                "results": len(frame),
                "retention_from_scheduled": (
                    len(frame) / population["scheduled_events"]
                ),
                "retention_from_outside": (
                    len(frame) / population["outside_candidates"]
                ),
                "long_results": int((frame["direction"] == 1).sum()),
                "short_results": int((frame["direction"] == -1).sum()),
                "active_months": int(frame["entry_month"].nunique()),
                "wins": int((frame["pnl_pips"] > 0).sum()),
                "win_rate": float((frame["pnl_pips"] > 0).mean()),
                "mean_pnl_pips": float(frame["pnl_pips"].mean()),
                "median_pnl_pips": float(frame["pnl_pips"].median()),
                "net_pnl_pips": float(frame["pnl_pips"].sum()),
                "profit_factor": profit_factor,
                "maximum_drawdown_pips": _maximum_drawdown(frame["pnl_pips"]),
                "worst_trade_pips": float(frame["pnl_pips"].min()),
                "mean_mfe_pips": float(frame["mfe_pips"].mean()),
                "mean_mae_pips": float(frame["mae_pips"].mean()),
                "mean_pnl_ci_low": low,
                "mean_pnl_ci_high": high,
                "underpowered": (
                    len(frame) < config.analysis.minimum_events_for_interpretation
                ),
            }
        )
    return pd.DataFrame(rows)


def selection_effects(
    statistics: pd.DataFrame,
) -> pd.DataFrame:
    selections = (
        (
            "reentry_selection",
            "open_timeout_90",
            "signal_cohort_open_timeout_90",
        ),
        (
            "poc_favorable_selection",
            "confirmed_timeout_all",
            "confirmed_timeout_favorable",
        ),
    )
    rows = []
    for (year, session), frame in statistics.groupby(
        ["sample_year", "session_name"], observed=True, sort=True
    ):
        indexed = frame.set_index("variant")
        for name, base_name, selected_name in selections:
            if base_name not in indexed.index or selected_name not in indexed.index:
                continue
            base = indexed.loc[base_name]
            selected = indexed.loc[selected_name]
            rows.append(
                {
                    "sample_year": year,
                    "session_name": session,
                    "contrast": name,
                    "base_variant": base_name,
                    "selected_variant": selected_name,
                    "base_count": int(base["results"]),
                    "selected_count": int(selected["results"]),
                    "retention_ratio": selected["results"] / base["results"],
                    "base_mean_pnl_pips": base["mean_pnl_pips"],
                    "selected_mean_pnl_pips": selected["mean_pnl_pips"],
                    "mean_difference_pips": (
                        selected["mean_pnl_pips"] - base["mean_pnl_pips"]
                    ),
                }
            )
    return pd.DataFrame(rows)


def paired_deltas(
    results: pd.DataFrame,
    config: OpeningAblationConfig,
    *,
    random_seed: int,
) -> pd.DataFrame:
    rows = []
    for group_index, ((year, session), frame) in enumerate(
        results.groupby(["sample_year", "session_name"], observed=True, sort=True)
    ):
        for contrast_index, (name, first_name, second_name) in enumerate(
            PAIRED_CONTRASTS
        ):
            first = frame[frame["variant"].eq(first_name)][
                ["event_id", "local_session_date", "pnl_pips"]
            ].rename(columns={"pnl_pips": "first_pnl_pips"})
            second = frame[frame["variant"].eq(second_name)][
                ["event_id", "pnl_pips"]
            ].rename(columns={"pnl_pips": "second_pnl_pips"})
            paired = first.merge(second, on="event_id", validate="one_to_one")
            if paired.empty:
                continue
            paired["delta_pips"] = (
                paired["second_pnl_pips"] - paired["first_pnl_pips"]
            )
            paired["entry_month"] = pd.to_datetime(
                paired["local_session_date"]
            ).dt.to_period("M").astype(str)
            low, high = _cluster_interval(
                paired,
                "delta_pips",
                resamples=config.analysis.bootstrap_resamples,
                confidence_level=config.analysis.confidence_level,
                random_seed=(
                    random_seed + group_index * 101 + contrast_index
                ),
            )
            rows.append(
                {
                    "sample_year": year,
                    "session_name": session,
                    "contrast": name,
                    "first_variant": first_name,
                    "second_variant": second_name,
                    "common_events": len(paired),
                    "mean_delta_pips": float(paired["delta_pips"].mean()),
                    "median_delta_pips": float(paired["delta_pips"].median()),
                    "share_improved": float((paired["delta_pips"] > 0).mean()),
                    "mean_delta_ci_low": low,
                    "mean_delta_ci_high": high,
                    "underpowered": (
                        len(paired)
                        < config.analysis.minimum_events_for_interpretation
                    ),
                }
            )
    return pd.DataFrame(rows)


def _monthly_statistics(results: pd.DataFrame) -> pd.DataFrame:
    frame = results.copy()
    frame["entry_month"] = pd.to_datetime(frame["local_session_date"]).dt.to_period(
        "M"
    ).astype(str)
    return (
        frame.groupby(
            ["sample_year", "session_name", "variant", "entry_month"],
            observed=True,
            sort=True,
        )
        .agg(
            results=("event_id", "size"),
            net_pnl_pips=("pnl_pips", "sum"),
            mean_pnl_pips=("pnl_pips", "mean"),
        )
        .reset_index()
    )


def _retention_funnel(events: pd.DataFrame, results: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (year, session), frame in events.groupby(
        ["sample_year", "session_name"], observed=True, sort=True
    ):
        outside = frame[
            frame["value_eligible"]
            & frame["value_state"].isin(["above_value", "below_value"])
        ]
        group_results = results[
            results["sample_year"].eq(year)
            & results["session_name"].eq(session)
        ]
        stages = {
            "scheduled": len(frame),
            "phase1_eligible": int(frame["eligible"].sum()),
            "value_eligible": int(frame["value_eligible"].sum()),
            "outside_candidate": len(outside),
            "reentry_signal": int(
                group_results[
                    group_results["variant"].eq("confirmed_timeout_all")
                ]["event_id"].nunique()
            ),
            "favorable_poc": int(
                group_results[
                    group_results["variant"].eq(
                        "confirmed_timeout_favorable"
                    )
                ]["event_id"].nunique()
            ),
            "phase4_full": int(
                group_results[group_results["variant"].eq("phase4_full")][
                    "event_id"
                ].nunique()
            ),
        }
        for stage, count in stages.items():
            rows.append(
                {
                    "sample_year": year,
                    "session_name": session,
                    "stage": stage,
                    "count": count,
                }
            )
    return pd.DataFrame(rows)


def execution_invariants(
    results: pd.DataFrame,
    strategy_config: OpeningValueStrategyConfig,
    ablation_config: OpeningAblationConfig,
    *,
    pip_size: float,
) -> dict[str, object]:
    opened = pd.to_datetime(results["event_timestamp_utc"], utc=True)
    entries = pd.to_datetime(results["entry_timestamp_utc"], utc=True)
    exits = pd.to_datetime(results["exit_timestamp_utc"], utc=True)
    signals = pd.to_datetime(results["signal_timestamp_utc"], utc=True)
    confirmed = results["variant"].str.startswith("confirmed") | results[
        "variant"
    ].eq("phase4_full")
    open_entry = ~confirmed
    horizons = np.select(
        [
            results["variant"].eq("open_timeout_30"),
            results["variant"].eq("open_timeout_60"),
        ],
        [30, 60],
        default=90,
    )
    profile_dates = pd.to_datetime(results["previous_profile_day"])
    fx_dates = pd.to_datetime(results["fx_trading_day"])
    checks = {
        "result_keys_unique": bool(
            ~results.duplicated(["sample_year", "event_id", "variant"]).any()
        ),
        "registered_variants_only": bool(
            set(results["variant"]).issubset(ablation_config.analysis.variants)
        ),
        "profile_days_strictly_prior": bool((profile_dates < fx_dates).all()),
        "direction_matches_outside_state": bool(
            (
                ((results["value_state"] == "above_value")
                 & (results["direction"] == -1))
                | ((results["value_state"] == "below_value")
                   & (results["direction"] == 1))
            ).all()
        ),
        "open_entries_at_session_timestamp": bool(
            (entries[open_entry] == opened[open_entry]).all()
        ),
        "confirmed_entries_follow_signal": bool(
            (
                entries[confirmed]
                == signals[confirmed] + pd.Timedelta(minutes=5)
            ).all()
        ),
        "confirmed_entries_by_deadline": bool(
            (
                entries[confirmed]
                <= opened[confirmed]
                + pd.Timedelta(
                    minutes=strategy_config.execution.entry_deadline_minutes
                )
            ).all()
        ),
        "exits_within_variant_horizon": bool(
            (exits <= opened + pd.to_timedelta(horizons, unit="m")).all()
        ),
        "positive_holding_period": bool((exits > entries).all()),
        "pnl_arithmetic": bool(
            np.allclose(
                results["pnl_pips"],
                results["direction"]
                * (results["exit_price"] - results["entry_price"])
                / pip_size,
            )
        ),
        "nonnegative_bar_excursions": bool(
            (results[["mfe_pips", "mae_pips"]].ge(0)).all().all()
        ),
    }
    return {"passed": all(checks.values()), "checks": checks}


def _population_table(events: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (year, session), frame in events.groupby(
        ["sample_year", "session_name"], observed=True, sort=True
    ):
        outside = frame[
            frame["value_eligible"]
            & frame["value_state"].isin(["above_value", "below_value"])
        ]
        rows.append(
            {
                "sample_year": year,
                "session_name": session,
                "scheduled_events": len(frame),
                "outside_candidates": len(outside),
            }
        )
    return pd.DataFrame(rows)


def _create_figures(
    statistics: pd.DataFrame, funnel: pd.DataFrame, output: Path
) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output.mkdir(parents=True, exist_ok=True)
    paths = []
    for year in sorted(statistics["sample_year"].unique()):
        frame = statistics[
            statistics["sample_year"].eq(year)
            & statistics["session_name"].eq("new_york")
        ]
        figure, axis = plt.subplots(figsize=(11, 5))
        axis.bar(frame["variant"], frame["mean_pnl_pips"])
        axis.axhline(0, color="black", linewidth=0.8)
        axis.tick_params(axis="x", rotation=60)
        axis.set(title=f"New York {year} mean net P&L", ylabel="Pips")
        figure.tight_layout()
        path = output / f"new_york_{year}_variant_mean.png"
        figure.savefig(path, dpi=150)
        plt.close(figure)
        paths.append(path)

    figure, axis = plt.subplots(figsize=(10, 5))
    pivot = funnel.pivot_table(
        index="stage",
        columns=["sample_year", "session_name"],
        values="count",
        aggfunc="first",
    )
    pivot.plot(kind="bar", ax=axis)
    axis.set(title="Phase-5 retention funnel", ylabel="Events", xlabel="Stage")
    axis.tick_params(axis="x", rotation=35)
    figure.tight_layout()
    path = output / "retention_funnel.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    paths.append(path)
    return paths


def _markdown_table(frame: pd.DataFrame) -> str:
    display = frame.round(4).fillna("NA")
    columns = list(display.columns)
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    rows = [
        "| " + " | ".join(str(value) for value in row) + " |"
        for row in display.itertuples(index=False, name=None)
    ]
    return "\n".join([header, separator, *rows])


def _render_report(
    statistics: pd.DataFrame,
    selections: pd.DataFrame,
    deltas: pd.DataFrame,
    invariants: dict[str, object],
) -> str:
    columns = [
        "sample_year",
        "session_name",
        "variant",
        "results",
        "mean_pnl_pips",
        "mean_pnl_ci_low",
        "mean_pnl_ci_high",
        "profit_factor",
        "maximum_drawdown_pips",
        "underpowered",
    ]
    checks = "\n".join(
        f"- [{'x' if passed else ' '}] `{name}`"
        for name, passed in invariants["checks"].items()
    )
    return f"""# Phase 5 — Opening-Auction Ablation

**Status: exploratory diagnosis; no validation decision**

Both 2024 and 2025 have already been inspected. Results are deliberately kept
separate and cannot authorize live trading.

## Variant statistics

{_markdown_table(statistics[columns])}

## Selection effects

{_markdown_table(selections)}

## Paired component deltas

A positive delta means the second variant improved net P&L on the same event.

{_markdown_table(deltas)}

## Execution invariants

{checks}

Variants without a stop expose directional and exit components only. They are
not deployable risk models.
"""


def run_phase5(
    project_root: Path,
    first: ProjectConfig,
    second: ProjectConfig,
    value_config: ValueStateConfig,
    strategy_config: OpeningValueStrategyConfig,
    ablation_config: OpeningAblationConfig,
) -> dict[str, Any]:
    intervals = (
        (first.research.data.start.isoformat(), first.research.data.end.isoformat()),
        (
            second.research.data.start.isoformat(),
            second.research.data.end.isoformat(),
        ),
    )
    if intervals != (
        ("2024-01-01", "2025-01-01"),
        ("2025-01-01", "2026-01-01"),
    ):
        raise ValueError("Phase-5 evidence intervals must remain 2024 and 2025")
    if first.sessions != second.sessions:
        raise ValueError("Phase-5 session contracts must match")
    if first.research.instrument != second.research.instrument:
        raise ValueError("Phase-5 instruments must match")

    all_events = []
    all_results = []
    inputs = {}
    for config, year in ((first, 2024), (second, 2025)):
        events, metadata = _load_upstream(project_root, config, value_config)
        events = events.copy()
        events["sample_year"] = year
        bars = load_m5_range(
            project_root,
            config,
            config.research.data.start,
            config.research.data.end,
        )
        results = simulate_opening_ablation(
            events,
            bars,
            strategy_config,
            ablation_config,
            pip_size=config.research.instrument.pip_size,
            sample_year=year,
        )
        all_events.append(events)
        all_results.append(results)
        inputs[str(year)] = metadata
    events = pd.concat(all_events, ignore_index=True)
    results = pd.concat(all_results, ignore_index=True)
    populations = _population_table(events)
    statistics = variant_statistics(
        results,
        populations,
        ablation_config,
        random_seed=first.research.study.random_seed,
    )
    selections = selection_effects(statistics)
    deltas = paired_deltas(
        results,
        ablation_config,
        random_seed=first.research.study.random_seed,
    )
    monthly = _monthly_statistics(results)
    funnel = _retention_funnel(events, results)
    invariants = execution_invariants(
        results,
        strategy_config,
        ablation_config,
        pip_size=first.research.instrument.pip_size,
    )
    upstream_valid = all(
        inputs[str(year)][phase]["data_quality_valid"]
        for year in (2024, 2025)
        for phase in ("phase1", "phase2")
    )

    run_id = phase5_run_id(
        first, second, value_config, strategy_config, ablation_config
    )
    processed = resolve_within_project(
        project_root, first.research.data.paths.processed
    )
    output = processed / "reports" / "phase5" / run_id
    output.mkdir(parents=True, exist_ok=True)
    results.to_parquet(output / "ablation_results.parquet", index=False)
    funnel.to_csv(output / "retention_funnel.csv", index=False)
    statistics.to_csv(output / "variant_statistics.csv", index=False)
    selections.to_csv(output / "selection_effects.csv", index=False)
    deltas.to_csv(output / "paired_deltas.csv", index=False)
    monthly.to_csv(output / "monthly_statistics.csv", index=False)
    data_quality = {
        "valid": bool(invariants["passed"] and upstream_valid),
        "execution_invariants": invariants,
        "upstream": inputs,
        "interpretation": "exploratory_only_no_validation_gate",
    }
    _write_json(data_quality, output / "data_quality.json")
    figures = _create_figures(statistics, funnel, output / "figures")
    report = _render_report(statistics, selections, deltas, invariants)
    (output / "report.md").write_text(report, encoding="utf-8")

    combined_config = {
        "first": first.model_dump(mode="json"),
        "second": second.model_dump(mode="json"),
        "value_state": value_config.model_dump(mode="json"),
        "opening_value": strategy_config.model_dump(mode="json"),
        "ablation": ablation_config.model_dump(mode="json"),
    }
    manifest = {
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "research_status": "exploratory_only_no_validation_decision",
        "config": combined_config,
        "config_sha256": _hash_json(combined_config),
        "git": _git_state(project_root),
        "inputs": inputs,
        "data_quality_valid": data_quality["valid"],
        "runtime": {
            "python": platform.python_version(),
            "packages": {
                package: importlib.metadata.version(package)
                for package in ("numpy", "pandas", "pyarrow", "matplotlib")
            },
        },
        "rows": {
            "events": len(events),
            "ablation_results": len(results),
            "variant_statistics": len(statistics),
            "selection_effects": len(selections),
            "paired_deltas": len(deltas),
            "monthly_statistics": len(monthly),
        },
        "artifacts": _artifact_records(output),
        "figures": [str(path.relative_to(output)) for path in figures],
    }
    _write_json(manifest, output / "run_manifest.json")
    return {
        **manifest,
        "output_directory": str(output.relative_to(project_root)),
        "report": str((output / "report.md").relative_to(project_root)),
    }
