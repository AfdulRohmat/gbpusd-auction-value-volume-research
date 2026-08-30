"""Orchestration and reporting for the Phase-8 balance-boundary strategy."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from gbpusd_research.config import (
    AuctionTaxonomyConfig,
    BalanceBoundaryStrategyConfig,
    ProjectConfig,
)
from gbpusd_research.data.pipeline import load_m5_range
from gbpusd_research.features.sessions import build_session_calendar
from gbpusd_research.research.auction_state_taxonomy import (
    build_state_episodes,
    build_state_timeline,
    build_state_transitions,
)
from gbpusd_research.research.balance_boundary_strategy import (
    build_analysis_trades,
    simulate_balance_boundary,
)
from gbpusd_research.research.phase4 import _artifact_records, _git_state
from gbpusd_research.research.phase6 import (
    _cluster_interval,
    monthly_statistics,
    variant_statistics,
)
from gbpusd_research.utils.paths import resolve_within_project


def _hash_json(content: object) -> str:
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, default=str).encode()
    ).hexdigest()


def _write_json(content: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(content, indent=2, sort_keys=True), encoding="utf-8")


def phase8_run_id(
    first: ProjectConfig,
    second: ProjectConfig,
    taxonomy: AuctionTaxonomyConfig,
    strategy: BalanceBoundaryStrategyConfig,
) -> str:
    content = {
        "first": first.model_dump(mode="json"),
        "second": second.model_dump(mode="json"),
        "taxonomy": taxonomy.model_dump(mode="json"),
        "balance_boundary": strategy.model_dump(mode="json"),
    }
    return (
        f"{first.research.data.start:%Y%m%d}_"
        f"{second.research.data.end:%Y%m%d}_{_hash_json(content)[:8]}"
    )


def event_funnel(events: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (year, session), group in events.groupby(
        ["sample_year", "session_name"], observed=True, sort=True
    ):
        complete = ~group["strategy_exclusion_reason"].eq(
            "incomplete_session_window"
        )
        stages = {
            "scheduled": len(group),
            "state_available": int(group["available_at"].notna().sum()),
            "observable_balance": int(
                group["observable_state"].eq("balance").sum()
            ),
            "valid_frozen_boundary": int(group["context_status"].eq("eligible").sum()),
            "complete_session_window": int(
                (group["context_status"].eq("eligible") & complete).sum()
            ),
            "valid_trigger": int(group["trigger_setup"].notna().sum()),
            "rejection_trigger": int(group["trigger_setup"].eq("rejection").sum()),
            "acceptance_trigger": int(
                group["trigger_setup"].eq("acceptance").sum()
            ),
            "traded": int(group["trade_executed"].sum()),
        }
        for stage, count in stages.items():
            rows.append(
                {
                    "sample_year": int(year),
                    "session_name": session,
                    "stage": stage,
                    "count": int(count),
                }
            )
    return pd.DataFrame(rows)


def exclusion_reasons(events: pd.DataFrame) -> pd.DataFrame:
    return (
        events[~events["trade_executed"]]
        .groupby(
            ["sample_year", "session_name", "strategy_exclusion_reason"],
            observed=True,
            sort=True,
            dropna=False,
        )
        .size()
        .rename("events")
        .reset_index()
    )


def setup_statistics(
    setup_trades: pd.DataFrame,
    config: BalanceBoundaryStrategyConfig,
    *,
    random_seed: int,
) -> pd.DataFrame:
    rows = []
    grouped = setup_trades.groupby(
        ["sample_year", "session_name", "setup", "variant"],
        observed=True,
        sort=True,
    )
    for group_index, (keys, group) in enumerate(grouped):
        year, session, setup, variant = keys
        frame = group.copy()
        frame["entry_month"] = pd.to_datetime(
            frame["local_session_date"]
        ).dt.to_period("M").astype(str)
        low, high = _cluster_interval(
            frame,
            "r_multiple",
            resamples=config.analysis.bootstrap_resamples,
            confidence_level=config.analysis.confidence_level,
            random_seed=random_seed + group_index,
        )
        winners = frame.loc[frame["r_multiple"] > 0, "r_multiple"]
        losers = frame.loc[frame["r_multiple"] < 0, "r_multiple"]
        rows.append(
            {
                "sample_year": int(year),
                "session_name": session,
                "setup": setup,
                "variant": variant,
                "trades": len(frame),
                "trades_per_calendar_month": len(frame) / 12,
                "win_rate": float((frame["r_multiple"] > 0).mean()),
                "mean_winner_r": (
                    float(winners.mean()) if not winners.empty else np.nan
                ),
                "mean_loser_r": float(losers.mean()) if not losers.empty else np.nan,
                "expectancy_r": float(frame["r_multiple"].mean()),
                "net_r": float(frame["r_multiple"].sum()),
                "profit_factor": (
                    float(winners.sum() / -losers.sum())
                    if not losers.empty
                    else (np.inf if not winners.empty else np.nan)
                ),
                "mean_nominal_reward_r": float(
                    frame["nominal_reward_r"].dropna().mean()
                ),
                "mean_holding_minutes": float(frame["holding_minutes"].mean()),
                "mean_mfe_r": float(frame["mfe_r"].mean()),
                "mean_mae_r": float(frame["mae_r"].mean()),
                "mean_r_ci_low": low,
                "mean_r_ci_high": high,
                "underpowered": bool(
                    len(frame) < config.analysis.minimum_events_for_interpretation
                ),
            }
        )
    return pd.DataFrame(rows)


def exit_statistics(setup_trades: pd.DataFrame) -> pd.DataFrame:
    return (
        setup_trades.groupby(
            ["sample_year", "variant", "exit_reason"],
            observed=True,
            sort=True,
        )
        .agg(
            trades=("event_id", "size"),
            mean_r=("r_multiple", "mean"),
            net_r=("r_multiple", "sum"),
            mean_holding_minutes=("holding_minutes", "mean"),
        )
        .reset_index()
    )


def paired_acceptance_deltas(
    setup_trades: pd.DataFrame,
    config: BalanceBoundaryStrategyConfig,
    *,
    random_seed: int,
) -> pd.DataFrame:
    acceptance = setup_trades[setup_trades["setup"].eq("acceptance")]
    rows = []
    for group_index, ((year, session), group) in enumerate(
        acceptance.groupby(
            ["sample_year", "session_name"], observed=True, sort=True
        )
    ):
        fixed = group[group["variant"].eq("acceptance_fixed_2r")][
            ["event_id", "local_session_date", "r_multiple"]
        ].rename(columns={"r_multiple": "fixed_r"})
        trailing = group[
            group["variant"].eq("acceptance_trailing_session")
        ][["event_id", "r_multiple"]].rename(columns={"r_multiple": "trailing_r"})
        paired = fixed.merge(trailing, on="event_id", validate="one_to_one")
        paired["delta_r"] = paired["trailing_r"] - paired["fixed_r"]
        paired["entry_month"] = pd.to_datetime(
            paired["local_session_date"]
        ).dt.to_period("M").astype(str)
        low, high = _cluster_interval(
            paired,
            "delta_r",
            resamples=config.analysis.bootstrap_resamples,
            confidence_level=config.analysis.confidence_level,
            random_seed=random_seed + group_index,
        )
        rows.append(
            {
                "sample_year": int(year),
                "session_name": session,
                "common_events": len(paired),
                "mean_trailing_minus_fixed_r": float(paired["delta_r"].mean()),
                "median_delta_r": float(paired["delta_r"].median()),
                "share_trailing_improved": float((paired["delta_r"] > 0).mean()),
                "mean_delta_ci_low": low,
                "mean_delta_ci_high": high,
            }
        )
    return pd.DataFrame(rows)


def execution_invariants(
    events: pd.DataFrame,
    setup_trades: pd.DataFrame,
    analysis_trades: pd.DataFrame,
    config: BalanceBoundaryStrategyConfig,
    *,
    pip_size: float,
) -> dict[str, object]:
    traded_events = events[events["trade_executed"]]
    entries = pd.to_datetime(setup_trades["entry_timestamp_utc"], utc=True)
    signals = pd.to_datetime(setup_trades["signal_available_at"], utc=True)
    trigger_bars = pd.to_datetime(
        setup_trades["trigger_bar_timestamp_utc"], utc=True
    )
    opened = pd.to_datetime(setup_trades["event_timestamp_utc"], utc=True)
    cutoffs = pd.to_datetime(setup_trades["cutoff_timestamp_utc"], utc=True)
    exits = pd.to_datetime(setup_trades["exit_timestamp_utc"], utc=True)
    slippage = config.execution.slippage_per_side_pips * pip_size
    expected_entry = np.where(
        setup_trades["direction"].eq(1),
        setup_trades["entry_ask_open"] + slippage,
        setup_trades["entry_bid_open"] - slippage,
    )
    rejection = setup_trades[setup_trades["setup"].eq("rejection")]
    acceptance = setup_trades[setup_trades["setup"].eq("acceptance")]
    upper_rejection = rejection["direction"].eq(-1)
    lower_rejection = rejection["direction"].eq(1)
    touch = config.context.boundary_touch_tolerance_pips * pip_size
    inside = config.context.rejection_close_inside_pips * pip_size
    outside = config.context.acceptance_close_outside_pips * pip_size
    rejection_geometry = (
        (
            upper_rejection
            & rejection["trigger_high"].ge(rejection["balance_high"] - touch)
            & rejection["trigger_close"].le(rejection["balance_high"] - inside)
        )
        | (
            lower_rejection
            & rejection["trigger_low"].le(rejection["balance_low"] + touch)
            & rejection["trigger_close"].ge(rejection["balance_low"] + inside)
        )
    )
    acceptance_geometry = (
        (
            acceptance["direction"].eq(1)
            & acceptance["signal_observable_state"].eq("imbalance_up")
            & acceptance["trigger_close"].ge(
                acceptance["balance_high"] + outside
            )
            & acceptance["acceptance_prior_close"].ge(
                acceptance["balance_high"] + outside
            )
        )
        | (
            acceptance["direction"].eq(-1)
            & acceptance["signal_observable_state"].eq("imbalance_down")
            & acceptance["trigger_close"].le(acceptance["balance_low"] - outside)
            & acceptance["acceptance_prior_close"].le(
                acceptance["balance_low"] - outside
            )
        )
    )
    counts = setup_trades.groupby(
        ["sample_year", "event_id", "setup"], observed=True
    )["variant"].nunique()
    expected_counts = counts.index.get_level_values("setup").map(
        {"rejection": 1, "acceptance": 2}
    )
    acceptance_shared = acceptance.groupby(
        ["sample_year", "event_id"], observed=True
    ).agg(
        entries=("entry_price", "nunique"),
        stops=("initial_stop_trigger_price", "nunique"),
        risks=("initial_risk_pips", "nunique"),
    )
    trailing = setup_trades[
        setup_trades["variant"].eq("acceptance_trailing_session")
    ]
    protective = trailing["direction"] * (
        trailing["final_stop_trigger_price"]
        - trailing["initial_stop_trigger_price"]
    )
    fixed = setup_trades[
        setup_trades["variant"].eq("acceptance_fixed_2r")
    ]
    portfolios = analysis_trades[
        analysis_trades["analysis_variant"].isin(config.analysis.portfolio_variants)
    ]
    portfolio_counts = portfolios.groupby(
        ["sample_year", "event_id"], observed=True
    )["analysis_variant"].nunique()
    context = events[events["context_status"].eq("eligible")]
    checks = {
        "event_keys_unique": bool(
            ~events.duplicated(["sample_year", "event_id"]).any()
        ),
        "setup_trade_keys_unique": bool(
            ~setup_trades.duplicated(["sample_year", "event_id", "variant"]).any()
        ),
        "analysis_trade_keys_unique": bool(
            ~analysis_trades.duplicated(
                ["sample_year", "event_id", "analysis_variant"]
            ).any()
        ),
        "context_state_point_in_time": bool(
            (
                pd.to_datetime(context["available_at"], utc=True)
                <= pd.to_datetime(context["event_timestamp_utc"], utc=True)
            ).all()
        ),
        "boundary_frozen_at_open": bool(
            (
                pd.to_datetime(context["boundary_available_at_max"], utc=True)
                <= pd.to_datetime(context["event_timestamp_utc"], utc=True)
            ).all()
        ),
        "traded_only_from_opening_balance": bool(
            traded_events["observable_state"].eq("balance").all()
        ),
        "one_setup_per_traded_event": bool(
            ~traded_events.duplicated(["sample_year", "event_id"]).any()
            and len(traded_events)
            == setup_trades[["sample_year", "event_id"]].drop_duplicates().shape[0]
        ),
        "registered_setup_result_counts": bool(
            (counts.to_numpy() == expected_counts.to_numpy()).all()
        ),
        "signal_is_completed_bar": bool(
            (signals == trigger_bars + np.timedelta64(5, "m")).all()
        ),
        "entry_is_next_bar_open": bool((entries == signals).all()),
        "trigger_inside_registered_window": bool(
            (
                trigger_bars.ge(opened)
                & trigger_bars.lt(
                    opened
                    + np.timedelta64(config.context.signal_window_minutes, "m")
                )
            ).all()
        ),
        "entry_quote_and_slippage_exact": bool(
            np.allclose(setup_trades["entry_price"], expected_entry)
        ),
        "positive_finite_initial_risk": bool(
            np.isfinite(setup_trades["initial_risk_pips"]).all()
            and setup_trades["initial_risk_pips"].gt(0).all()
        ),
        "rejection_geometry_exact": bool(
            rejection_geometry.all()
            and rejection["signal_raw_state"]
            .isin(config.context.rejection_raw_states)
            .all()
            and rejection["signal_observable_state"].eq("balance").all()
            and rejection["signal_observable_episode_id"]
            .eq(rejection["observable_episode_id"])
            .all()
            and rejection["nominal_reward_r"]
            .ge(config.execution.minimum_rotation_reward_to_risk)
            .all()
        ),
        "rotation_target_is_frozen_midpoint": bool(
            np.allclose(rejection["target_fill_price"], rejection["balance_midpoint"])
        ),
        "acceptance_geometry_and_confirmation_exact": bool(
            acceptance_geometry.all()
            and acceptance["acceptance_closes_outside"]
            .eq(config.context.acceptance_consecutive_closes)
            .all()
            and acceptance["acceptance_transition_from_episode_id"]
            .eq(acceptance["observable_episode_id"])
            .all()
            and acceptance["acceptance_transition_to_episode_id"]
            .eq(acceptance["signal_observable_episode_id"])
            .all()
        ),
        "fixed_breakout_target_is_registered_r": bool(
            np.allclose(
                fixed["nominal_reward_r"],
                config.execution.breakout_target_r_multiple,
            )
        ),
        "acceptance_variants_share_execution": bool(
            (acceptance_shared == 1).all().all()
        ),
        "two_combined_routes_per_traded_event": bool(
            (portfolio_counts == len(config.analysis.portfolio_variants)).all()
            and len(portfolio_counts) == len(traded_events)
        ),
        "exits_after_entry": bool((exits > entries).all()),
        "exits_by_session_cutoff": bool((exits <= cutoffs).all()),
        "ambiguous_bars_are_stop_first": bool(
            setup_trades.loc[
                setup_trades["ambiguous_bar_stop_first"], "exit_reason"
            ]
            .isin(["stop", "trailing_stop"])
            .all()
        ),
        "trailing_stops_never_loosen": bool((protective >= -1e-12).all()),
        "finite_trade_arithmetic": bool(
            np.isfinite(
                setup_trades[
                    [
                        "entry_price",
                        "initial_risk_pips",
                        "exit_price",
                        "pnl_pips",
                        "r_multiple",
                    ]
                ].to_numpy(dtype=float)
            ).all()
        ),
    }
    return {"passed": bool(all(checks.values())), "checks": checks}


def _create_figures(
    statistics: pd.DataFrame,
    monthly: pd.DataFrame,
    funnel: pd.DataFrame,
    output: Path,
) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    combined = statistics[statistics["session_scope"].eq("combined")]
    labels = [f"{row.sample_year}\n{row.variant}" for row in combined.itertuples()]
    values = combined["expectancy_r"].to_numpy(float)
    low = values - combined["mean_r_ci_low"].to_numpy(float)
    high = combined["mean_r_ci_high"].to_numpy(float) - values
    figure, axis = plt.subplots(figsize=(12, 5))
    axis.errorbar(labels, values, yerr=np.vstack([low, high]), fmt="o", capsize=4)
    axis.axhline(0, color="black", linewidth=1)
    axis.set(title="Phase-8 expectancy by registered variant", ylabel="Mean R")
    axis.tick_params(axis="x", rotation=30)
    figure.tight_layout()
    path = output / "combined_expectancy.png"
    figure.savefig(path, dpi=160)
    plt.close(figure)
    paths.append(path)

    combined_monthly = monthly[
        monthly["variant"].isin(
            ["combined_fixed_2r", "combined_trailing_session"]
        )
    ]
    figure, axis = plt.subplots(figsize=(11, 5))
    for (year, variant), group in combined_monthly.groupby(
        ["sample_year", "variant"], observed=True, sort=True
    ):
        axis.plot(
            group["entry_month"],
            group["net_r"],
            marker="o",
            label=f"{year} {variant}",
        )
    axis.axhline(0, color="black", linewidth=1)
    axis.set(title="Combined-route monthly net R", ylabel="Net R")
    axis.tick_params(axis="x", rotation=75)
    axis.legend(fontsize=8)
    figure.tight_layout()
    path = output / "combined_monthly_net_r.png"
    figure.savefig(path, dpi=160)
    plt.close(figure)
    paths.append(path)

    trigger = funnel[funnel["stage"].isin(["observable_balance", "traded"])]
    labels = [
        f"{row.sample_year} {row.session_name}\n{row.stage}"
        for row in trigger.itertuples()
    ]
    figure, axis = plt.subplots(figsize=(11, 5))
    axis.bar(labels, trigger["count"])
    axis.set(title="Balance-opening retention into executed trades", ylabel="Events")
    axis.tick_params(axis="x", rotation=35)
    figure.tight_layout()
    path = output / "event_retention.png"
    figure.savefig(path, dpi=160)
    plt.close(figure)
    paths.append(path)
    return paths


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_No rows._"
    display = frame.round(3).fillna("NA")
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
    setups: pd.DataFrame,
    monthly: pd.DataFrame,
    deltas: pd.DataFrame,
    funnel: pd.DataFrame,
    exclusions: pd.DataFrame,
    invariants: dict[str, object],
) -> str:
    combined = statistics[statistics["session_scope"].eq("combined")][
        [
            "sample_year",
            "variant",
            "trades",
            "trades_per_calendar_month",
            "win_rate",
            "mean_winner_r",
            "mean_loser_r",
            "payoff_ratio",
            "expectancy_r",
            "net_r",
            "profit_factor",
            "maximum_drawdown_r",
            "mean_r_ci_low",
            "mean_r_ci_high",
        ]
    ]
    setup_view = setups[
        [
            "sample_year",
            "session_name",
            "setup",
            "variant",
            "trades",
            "win_rate",
            "mean_nominal_reward_r",
            "expectancy_r",
            "net_r",
            "profit_factor",
            "mean_r_ci_low",
            "mean_r_ci_high",
        ]
    ]
    monthly_summary = (
        monthly.groupby(["sample_year", "variant"], observed=True, sort=True)
        .agg(
            months=("entry_month", "size"),
            mean_trades_per_month=("trades", "mean"),
            positive_months=("positive_month", "sum"),
            mean_monthly_r=("net_r", "mean"),
            worst_month_r=("net_r", "min"),
            best_month_r=("net_r", "max"),
        )
        .reset_index()
    )
    return f"""# Phase 8 Balance-Boundary Strategy Report

**Research status:** exploratory development/replication; no validation decision
**Execution invariants:** {"PASS" if invariants["passed"] else "FAIL"}

## Event funnel

{_markdown_table(funnel)}

## Exclusion reasons

{_markdown_table(exclusions)}

## Combined London/New York results

{_markdown_table(combined)}

## Setup and session decomposition

{_markdown_table(setup_view)}

## Monthly outcomes

{_markdown_table(monthly_summary)}

## Paired acceptance exit delta

{_markdown_table(deltas)}

## Interpretation boundary

The same 2024-2025 histories informed earlier phases. Phase 8 tests whether the
frozen balance boundary behaves as executable support/resistance, but it cannot
provide untouched validation or authorize live trading.
"""


def run_phase8(
    project_root: Path,
    first: ProjectConfig,
    second: ProjectConfig,
    taxonomy: AuctionTaxonomyConfig,
    strategy: BalanceBoundaryStrategyConfig,
) -> dict[str, Any]:
    intervals = (
        (first.research.data.start.isoformat(), first.research.data.end.isoformat()),
        (second.research.data.start.isoformat(), second.research.data.end.isoformat()),
    )
    if intervals != (
        ("2024-01-01", "2025-01-01"),
        ("2025-01-01", "2026-01-01"),
    ):
        raise ValueError("Phase-8 evidence intervals must remain 2024 and 2025")
    if first.sessions != second.sessions:
        raise ValueError("Phase-8 session contracts must match")
    if first.research.instrument != second.research.instrument:
        raise ValueError("Phase-8 instruments must match")

    all_events = []
    all_setup_trades = []
    all_transitions = []
    for project, year in ((first, 2024), (second, 2025)):
        bars = load_m5_range(
            project_root,
            project,
            project.research.data.start,
            project.research.data.end,
        )
        timeline = build_state_timeline(
            bars,
            taxonomy,
            pip_size=project.research.instrument.pip_size,
            sample_year=year,
        )
        timeline, episodes = build_state_episodes(
            timeline,
            taxonomy,
            pip_size=project.research.instrument.pip_size,
        )
        transitions = build_state_transitions(
            timeline,
            episodes,
            taxonomy,
            pip_size=project.research.instrument.pip_size,
        )
        start = pd.Timestamp(project.research.data.start, tz="UTC").to_pydatetime()
        end = pd.Timestamp(project.research.data.end, tz="UTC").to_pydatetime()
        calendar = build_session_calendar(start, end, project.sessions)
        events, setup_trades = simulate_balance_boundary(
            calendar,
            bars,
            timeline,
            episodes,
            transitions,
            strategy,
            project.sessions,
            pip_size=project.research.instrument.pip_size,
            sample_year=year,
        )
        all_events.append(events)
        all_setup_trades.append(setup_trades)
        all_transitions.append(transitions)

    events = pd.concat(all_events, ignore_index=True)
    setup_trades = pd.concat(all_setup_trades, ignore_index=True)
    transitions = pd.concat(all_transitions, ignore_index=True)
    analysis_trades = build_analysis_trades(setup_trades, strategy)
    for_stats = analysis_trades.drop(columns=["variant"]).rename(
        columns={"analysis_variant": "variant"}
    )
    random_seed = first.research.study.random_seed
    statistics = variant_statistics(
        for_stats,
        strategy,
        random_seed=random_seed,
    )
    setups = setup_statistics(setup_trades, strategy, random_seed=random_seed)
    exits = exit_statistics(setup_trades)
    deltas = paired_acceptance_deltas(
        setup_trades,
        strategy,
        random_seed=random_seed + 5000,
    )
    monthly = monthly_statistics(for_stats)
    funnel = event_funnel(events)
    exclusions = exclusion_reasons(events)
    invariants = execution_invariants(
        events,
        setup_trades,
        analysis_trades,
        strategy,
        pip_size=first.research.instrument.pip_size,
    )

    run_id = phase8_run_id(first, second, taxonomy, strategy)
    processed = resolve_within_project(
        project_root, first.research.data.paths.processed
    )
    output = processed / "reports" / "phase8" / run_id
    output.mkdir(parents=True, exist_ok=True)
    events.to_parquet(output / "boundary_events.parquet", index=False)
    setup_trades.to_parquet(output / "setup_trades.parquet", index=False)
    analysis_trades.to_parquet(output / "analysis_trades.parquet", index=False)
    funnel.to_csv(output / "event_funnel.csv", index=False)
    exclusions.to_csv(output / "exclusion_reasons.csv", index=False)
    statistics.to_csv(output / "variant_statistics.csv", index=False)
    setups.to_csv(output / "setup_statistics.csv", index=False)
    exits.to_csv(output / "exit_statistics.csv", index=False)
    deltas.to_csv(output / "paired_acceptance_deltas.csv", index=False)
    monthly.to_csv(output / "monthly_statistics.csv", index=False)
    data_quality = {
        "valid": bool(invariants["passed"]),
        "execution_invariants": invariants,
        "interpretation": "exploratory_development_replication_no_validation_gate",
    }
    _write_json(data_quality, output / "data_quality.json")
    figures = _create_figures(statistics, monthly, funnel, output / "figures")
    report = _render_report(
        statistics,
        setups,
        monthly,
        deltas,
        funnel,
        exclusions,
        invariants,
    )
    (output / "report.md").write_text(report, encoding="utf-8")

    combined_config = {
        "first": first.model_dump(mode="json"),
        "second": second.model_dump(mode="json"),
        "taxonomy": taxonomy.model_dump(mode="json"),
        "balance_boundary": strategy.model_dump(mode="json"),
    }
    manifest = {
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "research_status": "exploratory_development_replication_no_validation",
        "config": combined_config,
        "config_sha256": _hash_json(combined_config),
        "git": _git_state(project_root),
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
            "setup_trades": len(setup_trades),
            "analysis_trades": len(analysis_trades),
            "transitions": len(transitions),
            "variant_statistics": len(statistics),
            "setup_statistics": len(setups),
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
