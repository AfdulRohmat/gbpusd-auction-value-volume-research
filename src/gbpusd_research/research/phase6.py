"""Orchestration and reporting for the Phase-6 opening-auction state machine."""

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
    OpeningAuctionConfig,
    ProjectConfig,
    ValueStateConfig,
)
from gbpusd_research.data.pipeline import load_m5_range
from gbpusd_research.research.opening_auction_state_machine import (
    simulate_opening_auction,
)
from gbpusd_research.research.phase4 import (
    _artifact_records,
    _git_state,
    _load_upstream,
)
from gbpusd_research.utils.paths import resolve_within_project


def _hash_json(content: object) -> str:
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, default=str).encode()
    ).hexdigest()


def _write_json(content: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(content, indent=2, sort_keys=True), encoding="utf-8")


def phase6_run_id(
    first: ProjectConfig,
    second: ProjectConfig,
    value_config: ValueStateConfig,
    auction_config: OpeningAuctionConfig,
) -> str:
    content = {
        "first": first.model_dump(mode="json"),
        "second": second.model_dump(mode="json"),
        "value_state": value_config.model_dump(mode="json"),
        "opening_auction": auction_config.model_dump(mode="json"),
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
    estimates = np.empty(resamples)
    for index in range(resamples):
        choices = rng.integers(0, len(groups), size=len(groups))
        estimates[index] = np.concatenate([groups[item] for item in choices]).mean()
    alpha = (1 - confidence_level) / 2
    low, high = np.quantile(estimates, [alpha, 1 - alpha])
    return float(low), float(high)


def _maximum_drawdown(values: pd.Series) -> float:
    equity = np.concatenate(([0.0], values.cumsum().to_numpy(dtype=float)))
    return float(np.max(np.maximum.accumulate(equity) - equity))


def _scoped_trades(trades: pd.DataFrame) -> pd.DataFrame:
    sessions = trades.copy()
    sessions["session_scope"] = sessions["session_name"]
    combined = trades.copy()
    combined["session_scope"] = "combined"
    return pd.concat([sessions, combined], ignore_index=True)


def variant_statistics(
    trades: pd.DataFrame,
    config: OpeningAuctionConfig,
    *,
    random_seed: int,
) -> pd.DataFrame:
    frame = _scoped_trades(trades)
    frame["entry_month"] = pd.to_datetime(frame["local_session_date"]).dt.to_period(
        "M"
    ).astype(str)
    rows = []
    grouped = frame.groupby(
        ["sample_year", "session_scope", "variant"],
        observed=True,
        sort=True,
    )
    for group_index, ((year, scope, variant), group) in enumerate(grouped):
        group = group.sort_values("entry_timestamp_utc", kind="stable")
        winners = group.loc[group["r_multiple"] > 0, "r_multiple"]
        losers = group.loc[group["r_multiple"] < 0, "r_multiple"]
        gross_profit = float(winners.sum())
        gross_loss = float(-losers.sum())
        mean_win = float(winners.mean()) if not winners.empty else np.nan
        mean_loss = float(losers.mean()) if not losers.empty else np.nan
        low, high = _cluster_interval(
            group,
            "r_multiple",
            resamples=config.analysis.bootstrap_resamples,
            confidence_level=config.analysis.confidence_level,
            random_seed=random_seed + group_index,
        )
        rows.append(
            {
                "sample_year": int(year),
                "session_scope": scope,
                "variant": variant,
                "trades": len(group),
                "trades_per_calendar_month": len(group) / 12,
                "active_months": int(group["entry_month"].nunique()),
                "long_trades": int((group["direction"] == 1).sum()),
                "short_trades": int((group["direction"] == -1).sum()),
                "wins": int((group["r_multiple"] > 0).sum()),
                "win_rate": float((group["r_multiple"] > 0).mean()),
                "mean_winner_r": mean_win,
                "mean_loser_r": mean_loss,
                "payoff_ratio": (
                    mean_win / abs(mean_loss)
                    if np.isfinite(mean_win) and np.isfinite(mean_loss)
                    else np.nan
                ),
                "expectancy_r": float(group["r_multiple"].mean()),
                "median_r": float(group["r_multiple"].median()),
                "net_r": float(group["r_multiple"].sum()),
                "mean_pnl_pips": float(group["pnl_pips"].mean()),
                "net_pnl_pips": float(group["pnl_pips"].sum()),
                "profit_factor": (
                    gross_profit / gross_loss
                    if gross_loss > 0
                    else (np.inf if gross_profit > 0 else np.nan)
                ),
                "maximum_drawdown_r": _maximum_drawdown(group["r_multiple"]),
                "maximum_drawdown_pips": _maximum_drawdown(group["pnl_pips"]),
                "mean_mfe_r": float(group["mfe_r"].mean()),
                "mean_mae_r": float(group["mae_r"].mean()),
                "mean_r_ci_low": low,
                "mean_r_ci_high": high,
                "benchmark_expectancy_met": bool(
                    group["r_multiple"].mean()
                    >= config.analysis.benchmark_expectancy_r
                ),
                "underpowered": bool(
                    len(group) < config.analysis.minimum_events_for_interpretation
                ),
            }
        )
    return pd.DataFrame(rows)


def state_statistics(
    trades: pd.DataFrame,
    config: OpeningAuctionConfig,
    *,
    random_seed: int,
) -> pd.DataFrame:
    frame = trades.copy()
    frame["entry_month"] = pd.to_datetime(frame["local_session_date"]).dt.to_period(
        "M"
    ).astype(str)
    rows = []
    grouped = frame.groupby(
        ["sample_year", "session_name", "auction_state", "variant"],
        observed=True,
        sort=True,
    )
    for group_index, (keys, group) in enumerate(grouped):
        year, session, state, variant = keys
        winners = group.loc[group["r_multiple"] > 0, "r_multiple"]
        losers = group.loc[group["r_multiple"] < 0, "r_multiple"]
        low, high = _cluster_interval(
            group,
            "r_multiple",
            resamples=config.analysis.bootstrap_resamples,
            confidence_level=config.analysis.confidence_level,
            random_seed=random_seed + 1000 + group_index,
        )
        rows.append(
            {
                "sample_year": int(year),
                "session_name": session,
                "auction_state": state,
                "variant": variant,
                "trades": len(group),
                "win_rate": float((group["r_multiple"] > 0).mean()),
                "mean_winner_r": (
                    float(winners.mean()) if not winners.empty else np.nan
                ),
                "mean_loser_r": float(losers.mean()) if not losers.empty else np.nan,
                "expectancy_r": float(group["r_multiple"].mean()),
                "net_r": float(group["r_multiple"].sum()),
                "profit_factor": (
                    float(winners.sum() / -losers.sum())
                    if not losers.empty
                    else (np.inf if not winners.empty else np.nan)
                ),
                "mean_r_ci_low": low,
                "mean_r_ci_high": high,
                "underpowered": bool(
                    len(group) < config.analysis.minimum_events_for_interpretation
                ),
            }
        )
    return pd.DataFrame(rows)


def monthly_statistics(trades: pd.DataFrame) -> pd.DataFrame:
    frame = trades.copy()
    frame["entry_month"] = pd.to_datetime(frame["local_session_date"]).dt.to_period(
        "M"
    ).astype(str)
    return (
        frame.groupby(
            ["sample_year", "variant", "entry_month"],
            observed=True,
            sort=True,
        )
        .agg(
            trades=("event_id", "size"),
            wins=("r_multiple", lambda values: int((values > 0).sum())),
            net_r=("r_multiple", "sum"),
            mean_r=("r_multiple", "mean"),
            net_pnl_pips=("pnl_pips", "sum"),
        )
        .reset_index()
        .assign(
            win_rate=lambda value: value["wins"] / value["trades"],
            positive_month=lambda value: value["net_r"] > 0,
        )
    )


def paired_exit_deltas(
    trades: pd.DataFrame,
    config: OpeningAuctionConfig,
    *,
    random_seed: int,
) -> pd.DataFrame:
    contrasts = (
        ("session_hold_minus_fixed_2r", "fixed_2r", "session_hold"),
        ("trailing_minus_fixed_2r", "fixed_2r", "trailing_session"),
        ("trailing_minus_session_hold", "session_hold", "trailing_session"),
    )
    rows = []
    for group_index, ((year, session), group) in enumerate(
        trades.groupby(["sample_year", "session_name"], observed=True, sort=True)
    ):
        for contrast_index, (name, first_name, second_name) in enumerate(contrasts):
            first = group[group["variant"].eq(first_name)][
                ["event_id", "local_session_date", "r_multiple"]
            ].rename(columns={"r_multiple": "first_r"})
            second = group[group["variant"].eq(second_name)][
                ["event_id", "r_multiple"]
            ].rename(columns={"r_multiple": "second_r"})
            paired = first.merge(second, on="event_id", validate="one_to_one")
            paired["delta_r"] = paired["second_r"] - paired["first_r"]
            paired["entry_month"] = pd.to_datetime(
                paired["local_session_date"]
            ).dt.to_period("M").astype(str)
            low, high = _cluster_interval(
                paired,
                "delta_r",
                resamples=config.analysis.bootstrap_resamples,
                confidence_level=config.analysis.confidence_level,
                random_seed=random_seed + group_index * 10 + contrast_index,
            )
            rows.append(
                {
                    "sample_year": int(year),
                    "session_name": session,
                    "contrast": name,
                    "first_variant": first_name,
                    "second_variant": second_name,
                    "common_events": len(paired),
                    "mean_delta_r": float(paired["delta_r"].mean()),
                    "median_delta_r": float(paired["delta_r"].median()),
                    "share_improved": float((paired["delta_r"] > 0).mean()),
                    "mean_delta_ci_low": low,
                    "mean_delta_ci_high": high,
                }
            )
    return pd.DataFrame(rows)


def event_funnel(events: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (year, session), group in events.groupby(
        ["sample_year", "session_name"], observed=True, sort=True
    ):
        stages = {
            "scheduled": len(group),
            "phase1_eligible": int(group["phase1_eligible"].sum()),
            "complete_session_window": int(
                (
                    ~group["auction_exclusion_reason"].eq(
                        "incomplete_session_window"
                    )
                    & group["phase1_eligible"]
                ).sum()
            ),
            "classified": int(group["auction_state"].notna().sum()),
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


def execution_invariants(
    events: pd.DataFrame,
    trades: pd.DataFrame,
    config: OpeningAuctionConfig,
) -> dict[str, object]:
    traded_events = events[events["trade_executed"]]
    entries = pd.to_datetime(trades["entry_timestamp_utc"], utc=True)
    opened = pd.to_datetime(trades["event_timestamp_utc"], utc=True)
    exits = pd.to_datetime(trades["exit_timestamp_utc"], utc=True)
    cutoffs = pd.to_datetime(trades["cutoff_timestamp_utc"], utc=True)
    state_direction = (
        ((trades["auction_state"] == "imbalance_up") & (trades["direction"] == 1))
        | (
            (trades["auction_state"] == "imbalance_down")
            & (trades["direction"] == -1)
        )
        | ((trades["auction_state"] == "balance_high") & (trades["direction"] == -1))
        | ((trades["auction_state"] == "balance_low") & (trades["direction"] == 1))
    )
    counts = trades.groupby(["sample_year", "event_id"], observed=True)[
        "variant"
    ].nunique()
    shared = trades.groupby(["sample_year", "event_id"], observed=True).agg(
        states=("auction_state", "nunique"),
        entries=("entry_price", "nunique"),
        stops=("initial_stop_trigger_price", "nunique"),
        risks=("initial_risk_pips", "nunique"),
    )
    trailing = trades[trades["variant"].eq("trailing_session")]
    protective = trailing["direction"] * (
        trailing["final_stop_trigger_price"]
        - trailing["initial_stop_trigger_price"]
    )
    target_rows = trades[trades["exit_reason"].eq("target_2r")]
    checks = {
        "event_keys_unique": bool(
            ~events.duplicated(["sample_year", "event_id"]).any()
        ),
        "trade_keys_unique": bool(
            ~trades.duplicated(["sample_year", "event_id", "variant"]).any()
        ),
        "registered_variants_only": bool(
            set(trades["variant"]).issubset(config.analysis.variants)
        ),
        "one_result_per_variant": bool(
            (counts == len(config.analysis.variants)).all()
            and len(counts) == len(traded_events)
        ),
        "entry_exactly_after_observation": bool(
            (
                entries
                == opened
                + pd.Timedelta(minutes=config.classification.observation_minutes)
            ).all()
        ),
        "state_direction_mapping_exact": bool(state_direction.all()),
        "positive_initial_risk": bool((trades["initial_risk_pips"] > 0).all()),
        "variants_share_state_entry_stop_and_risk": bool((shared == 1).all().all()),
        "exits_after_entry": bool((exits > entries).all()),
        "exits_by_session_cutoff": bool((exits <= cutoffs).all()),
        "fixed_targets_realize_registered_r": bool(
            np.allclose(
                target_rows["r_multiple"],
                config.execution.target_r_multiple,
            )
        ),
        "ambiguous_fixed_bars_stop_first": bool(
            trades.loc[trades["ambiguous_bar_stop_first"], "exit_reason"]
            .isin(["stop", "trailing_stop"])
            .all()
        ),
        "trailing_stops_never_loosen": bool((protective >= -1e-12).all()),
        "finite_trade_arithmetic": bool(
            np.isfinite(
                trades[
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
    output: Path,
) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    combined = statistics[statistics["session_scope"].eq("combined")].copy()
    labels = [f"{row.sample_year}\n{row.variant}" for row in combined.itertuples()]
    values = combined["expectancy_r"].to_numpy(float)
    low = values - combined["mean_r_ci_low"].to_numpy(float)
    high = combined["mean_r_ci_high"].to_numpy(float) - values
    figure, axis = plt.subplots(figsize=(10, 5))
    axis.errorbar(labels, values, yerr=np.vstack([low, high]), fmt="o", capsize=4)
    axis.axhline(0, color="black", linewidth=1)
    axis.set_ylabel("Mean R per trade")
    axis.set_title("Combined-session expectancy with month-cluster intervals")
    figure.tight_layout()
    path = output / "combined_expectancy.png"
    figure.savefig(path, dpi=160)
    plt.close(figure)
    paths.append(path)

    figure, axis = plt.subplots(figsize=(11, 5))
    for (year, variant), group in monthly.groupby(
        ["sample_year", "variant"], observed=True, sort=True
    ):
        axis.plot(
            group["entry_month"],
            group["net_r"],
            marker="o",
            label=f"{year} {variant}",
        )
    axis.axhline(0, color="black", linewidth=1)
    axis.set_ylabel("Net R")
    axis.set_title("Combined London/New York monthly outcome")
    axis.tick_params(axis="x", rotation=75)
    axis.legend(fontsize=8, ncol=2)
    figure.tight_layout()
    path = output / "monthly_net_r.png"
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
    states: pd.DataFrame,
    monthly: pd.DataFrame,
    deltas: pd.DataFrame,
    funnel: pd.DataFrame,
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
    session = statistics[~statistics["session_scope"].eq("combined")][
        [
            "sample_year",
            "session_scope",
            "variant",
            "trades",
            "win_rate",
            "expectancy_r",
            "net_r",
            "profit_factor",
            "mean_r_ci_low",
            "mean_r_ci_high",
        ]
    ]
    state_view = states[
        [
            "sample_year",
            "session_name",
            "auction_state",
            "variant",
            "trades",
            "win_rate",
            "expectancy_r",
            "net_r",
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
    return f"""# Phase 6 Opening-Auction State-Machine Report

**Research status:** exploratory only; no untouched validation decision  
**Execution invariants:** {"PASS" if invariants["passed"] else "FAIL"}

## Retention

{_markdown_table(funnel)}

## Combined London/New York results

{_markdown_table(combined)}

`fixed_2r` win rate must be interpreted with its realized payoff and timeout
exits. A nominal 2R target is not assumed to equal the average winner.

## Session results

{_markdown_table(session)}

## State decomposition

{_markdown_table(state_view)}

## Monthly outcomes

{_markdown_table(monthly_summary)}

## Paired exit effects

{_markdown_table(deltas)}

## Interpretation boundary

The same 2024--2025 histories informed earlier research. These results diagnose
the frozen state machine but cannot authorize live trading. Any promising rule
requires a new untouched forward period and portfolio-level risk simulation.
"""


def run_phase6(
    project_root: Path,
    first: ProjectConfig,
    second: ProjectConfig,
    value_config: ValueStateConfig,
    auction_config: OpeningAuctionConfig,
) -> dict[str, Any]:
    intervals = (
        (first.research.data.start.isoformat(), first.research.data.end.isoformat()),
        (second.research.data.start.isoformat(), second.research.data.end.isoformat()),
    )
    if intervals != (
        ("2024-01-01", "2025-01-01"),
        ("2025-01-01", "2026-01-01"),
    ):
        raise ValueError("Phase-6 evidence intervals must remain 2024 and 2025")
    if first.sessions != second.sessions:
        raise ValueError("Phase-6 session contracts must match")
    if first.research.instrument != second.research.instrument:
        raise ValueError("Phase-6 instruments must match")

    all_events = []
    all_trades = []
    inputs = {}
    for project_config, year in ((first, 2024), (second, 2025)):
        source_events, metadata = _load_upstream(
            project_root, project_config, value_config
        )
        bars = load_m5_range(
            project_root,
            project_config,
            project_config.research.data.start,
            project_config.research.data.end,
        )
        events, trades = simulate_opening_auction(
            source_events,
            bars,
            auction_config,
            project_config.sessions,
            pip_size=project_config.research.instrument.pip_size,
            sample_year=year,
        )
        all_events.append(events)
        all_trades.append(trades)
        inputs[str(year)] = metadata

    events = pd.concat(all_events, ignore_index=True)
    trades = pd.concat(all_trades, ignore_index=True)
    random_seed = first.research.study.random_seed
    statistics = variant_statistics(trades, auction_config, random_seed=random_seed)
    states = state_statistics(trades, auction_config, random_seed=random_seed)
    monthly = monthly_statistics(trades)
    deltas = paired_exit_deltas(trades, auction_config, random_seed=random_seed)
    funnel = event_funnel(events)
    invariants = execution_invariants(events, trades, auction_config)
    upstream_valid = all(
        inputs[str(year)][phase]["data_quality_valid"]
        for year in (2024, 2025)
        for phase in ("phase1", "phase2")
    )

    run_id = phase6_run_id(first, second, value_config, auction_config)
    processed = resolve_within_project(
        project_root, first.research.data.paths.processed
    )
    output = processed / "reports" / "phase6" / run_id
    output.mkdir(parents=True, exist_ok=True)
    events.to_parquet(output / "auction_events.parquet", index=False)
    trades.to_parquet(output / "auction_trades.parquet", index=False)
    funnel.to_csv(output / "event_funnel.csv", index=False)
    statistics.to_csv(output / "variant_statistics.csv", index=False)
    states.to_csv(output / "state_statistics.csv", index=False)
    deltas.to_csv(output / "paired_exit_deltas.csv", index=False)
    monthly.to_csv(output / "monthly_statistics.csv", index=False)
    data_quality = {
        "valid": bool(invariants["passed"] and upstream_valid),
        "execution_invariants": invariants,
        "upstream": inputs,
        "interpretation": "exploratory_only_no_validation_gate",
    }
    _write_json(data_quality, output / "data_quality.json")
    figures = _create_figures(statistics, monthly, output / "figures")
    report = _render_report(statistics, states, monthly, deltas, funnel, invariants)
    (output / "report.md").write_text(report, encoding="utf-8")

    combined_config = {
        "first": first.model_dump(mode="json"),
        "second": second.model_dump(mode="json"),
        "value_state": value_config.model_dump(mode="json"),
        "opening_auction": auction_config.model_dump(mode="json"),
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
            "trades": len(trades),
            "variant_statistics": len(statistics),
            "state_statistics": len(states),
            "paired_exit_deltas": len(deltas),
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
