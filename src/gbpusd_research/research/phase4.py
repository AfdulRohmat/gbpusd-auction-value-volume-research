"""Orchestration and reporting for frozen Phase-4 strategy validation."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from gbpusd_research.config import (
    OpeningValueStrategyConfig,
    ProjectConfig,
    ValueStateConfig,
)
from gbpusd_research.data.pipeline import load_m5_range
from gbpusd_research.research.opening_value_strategy import (
    simulate_opening_value_strategy,
)
from gbpusd_research.research.phase1 import phase1_run_id
from gbpusd_research.research.phase2 import phase2_run_id
from gbpusd_research.utils.paths import resolve_within_project


def _hash_json(content: object) -> str:
    payload = json.dumps(content, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(content: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(content, indent=2, sort_keys=True), encoding="utf-8")


def _git_state(project_root: Path) -> dict[str, object]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "commit": commit.stdout.strip() if commit.returncode == 0 else None,
        "dirty": bool(status.stdout.strip()) if status.returncode == 0 else None,
    }


def phase4_run_id(
    development: ProjectConfig,
    validation: ProjectConfig,
    value_config: ValueStateConfig,
    strategy_config: OpeningValueStrategyConfig,
) -> str:
    """Return a deterministic identifier for the complete frozen validation."""

    combined = {
        "development": development.model_dump(mode="json"),
        "validation": validation.model_dump(mode="json"),
        "value_state": value_config.model_dump(mode="json"),
        "opening_value": strategy_config.model_dump(mode="json"),
    }
    return (
        f"{development.research.data.start:%Y%m%d}_"
        f"{validation.research.data.end:%Y%m%d}_{_hash_json(combined)[:8]}"
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"Missing required upstream artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_upstream(
    project_root: Path,
    config: ProjectConfig,
    value_config: ValueStateConfig,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    processed = resolve_within_project(
        project_root, config.research.data.paths.processed
    )
    phase1_id = phase1_run_id(config)
    phase2_id = phase2_run_id(config, value_config)
    phase1_dir = processed / "reports" / "phase1" / phase1_id
    phase2_dir = processed / "reports" / "phase2" / phase2_id
    phase1_manifest = _read_json(phase1_dir / "run_manifest.json")
    phase1_quality = _read_json(phase1_dir / "data_quality.json")
    phase2_manifest = _read_json(phase2_dir / "run_manifest.json")
    phase2_quality = _read_json(phase2_dir / "data_quality.json")
    events_path = phase2_dir / "value_events.parquet"
    if not events_path.is_file():
        raise ValueError(f"Missing required upstream artifact: {events_path}")
    if phase1_manifest.get("run_id") != phase1_id:
        raise ValueError("Phase-1 manifest run ID does not match its configuration")
    if phase2_manifest.get("run_id") != phase2_id:
        raise ValueError("Phase-2 manifest run ID does not match its configuration")
    events = pd.read_parquet(events_path)
    metadata = {
        "phase1": {
            "run_id": phase1_id,
            "manifest_sha256": _hash_file(phase1_dir / "run_manifest.json"),
            "research_gate": phase1_manifest["research_gate"],
            "data_quality_valid": bool(phase1_quality["valid"]),
        },
        "phase2": {
            "run_id": phase2_id,
            "manifest_sha256": _hash_file(phase2_dir / "run_manifest.json"),
            "data_quality_valid": bool(phase2_quality["valid"]),
            "source_archives": phase2_manifest.get("source_archives", []),
            "source_snapshot_sha256": phase2_manifest.get(
                "source_snapshot_sha256"
            ),
        },
    }
    return events, metadata


def _month_cluster_interval(
    trades: pd.DataFrame,
    *,
    resamples: int,
    confidence_level: float,
    random_seed: int,
) -> tuple[float, float]:
    if trades.empty:
        return np.nan, np.nan
    grouped = [
        group["r_multiple"].to_numpy(dtype=float)
        for _, group in trades.groupby("entry_month", observed=True, sort=True)
    ]
    rng = np.random.default_rng(random_seed)
    estimates = np.empty(resamples)
    cluster_count = len(grouped)
    for index in range(resamples):
        selections = rng.integers(0, cluster_count, size=cluster_count)
        sample = np.concatenate([grouped[item] for item in selections])
        estimates[index] = sample.mean()
    alpha = (1 - confidence_level) / 2
    low, high = np.quantile(estimates, [alpha, 1 - alpha])
    return float(low), float(high)


def _maximum_drawdown(values: pd.Series) -> float:
    equity = np.concatenate(([0.0], values.cumsum().to_numpy(dtype=float)))
    return float(np.max(np.maximum.accumulate(equity) - equity))


def performance_statistics(
    events: pd.DataFrame,
    config: OpeningValueStrategyConfig,
    *,
    random_seed: int,
) -> pd.DataFrame:
    """Summarize each sample/session without pooling development and validation."""

    rows: list[dict[str, object]] = []
    grouped = events.groupby(
        ["sample_role", "session_name"], observed=True, sort=True
    )
    for group_index, ((sample_role, session_name), frame) in enumerate(grouped):
        trades = frame[frame["trade_executed"]].copy()
        trades = trades.sort_values("entry_timestamp_utc", kind="stable")
        if not trades.empty:
            trades["entry_month"] = pd.to_datetime(
                trades["local_session_date"]
            ).dt.to_period("M").astype(str)
        positive = trades.loc[trades["pnl_pips"] > 0, "r_multiple"].sum()
        negative = -trades.loc[trades["pnl_pips"] < 0, "r_multiple"].sum()
        profit_factor = (
            float(positive / negative)
            if negative > 0
            else (np.inf if positive > 0 else np.nan)
        )
        ci_low, ci_high = _month_cluster_interval(
            trades,
            resamples=config.analysis.bootstrap_resamples,
            confidence_level=config.analysis.confidence_level,
            random_seed=random_seed + group_index,
        )
        rows.append(
            {
                "sample_role": sample_role,
                "session_name": session_name,
                "scheduled_events": len(frame),
                "phase1_eligible_events": int(frame["phase1_eligible"].sum()),
                "value_eligible_events": int(frame["value_eligible"].sum()),
                "candidate_events": int(frame["candidate"].sum()),
                "signal_events": int(frame["signal_found"].sum()),
                "trades": len(trades),
                "long_trades": int((trades["direction"] == 1).sum()),
                "short_trades": int((trades["direction"] == -1).sum()),
                "active_months": int(trades.get("entry_month", pd.Series()).nunique()),
                "wins": int((trades["pnl_pips"] > 0).sum()),
                "win_rate": float((trades["pnl_pips"] > 0).mean()),
                "target_exits": int((trades["exit_reason"] == "target").sum()),
                "stop_exits": int((trades["exit_reason"] == "stop").sum()),
                "timeout_exits": int((trades["exit_reason"] == "timeout").sum()),
                "mean_pnl_pips": float(trades["pnl_pips"].mean()),
                "median_pnl_pips": float(trades["pnl_pips"].median()),
                "mean_r": float(trades["r_multiple"].mean()),
                "median_r": float(trades["r_multiple"].median()),
                "mean_stressed_r": float(trades["stressed_r_multiple"].mean()),
                "gross_profit_r": float(positive),
                "gross_loss_r": float(negative),
                "profit_factor": profit_factor,
                "maximum_drawdown_r": (
                    _maximum_drawdown(trades["r_multiple"])
                    if not trades.empty
                    else np.nan
                ),
                "mean_initial_risk_pips": float(
                    trades["initial_risk_pips"].mean()
                ),
                "mean_reward_to_risk": float(trades["reward_to_risk"].mean()),
                "mean_r_ci_low": ci_low,
                "mean_r_ci_high": ci_high,
            }
        )
    return pd.DataFrame(rows)


def _event_funnel(events: pd.DataFrame) -> pd.DataFrame:
    stages = (
        ("scheduled", None),
        ("phase1_eligible", "phase1_eligible"),
        ("value_eligible", "value_eligible"),
        ("candidate", "candidate"),
        ("signal", "signal_found"),
        ("trade", "trade_executed"),
    )
    rows = []
    grouped = events.groupby(
        ["sample_role", "session_name"], observed=True, sort=True
    )
    for (sample_role, session_name), frame in grouped:
        for stage, column in stages:
            rows.append(
                {
                    "sample_role": sample_role,
                    "session_name": session_name,
                    "stage": stage,
                    "count": len(frame) if column is None else int(frame[column].sum()),
                }
            )
    return pd.DataFrame(rows)


def _monthly_statistics(events: pd.DataFrame) -> pd.DataFrame:
    trades = events[events["trade_executed"]].copy()
    if trades.empty:
        return pd.DataFrame(
            columns=[
                "sample_role",
                "session_name",
                "entry_month",
                "trades",
                "net_r",
                "mean_r",
                "stressed_net_r",
            ]
        )
    trades["entry_month"] = pd.to_datetime(trades["local_session_date"]).dt.to_period(
        "M"
    ).astype(str)
    return (
        trades.groupby(
            ["sample_role", "session_name", "entry_month"],
            observed=True,
            sort=True,
        )
        .agg(
            trades=("event_id", "size"),
            net_r=("r_multiple", "sum"),
            mean_r=("r_multiple", "mean"),
            stressed_net_r=("stressed_r_multiple", "sum"),
        )
        .reset_index()
    )


def _exclusion_statistics(events: pd.DataFrame) -> pd.DataFrame:
    excluded = events[~events["trade_executed"]].copy()
    excluded["strategy_exclusion_reason"] = excluded[
        "strategy_exclusion_reason"
    ].fillna("unspecified")
    return (
        excluded.groupby(
            [
                "sample_role",
                "session_name",
                "strategy_status",
                "strategy_exclusion_reason",
            ],
            observed=True,
            sort=True,
        )
        .size()
        .rename("count")
        .reset_index()
    )


def execution_invariants(
    events: pd.DataFrame,
    config: OpeningValueStrategyConfig,
    *,
    pip_size: float,
) -> dict[str, object]:
    """Audit persisted execution fields independently of performance outcomes."""

    trades = events[events["trade_executed"]].copy()
    candidates = events[events["candidate"]]
    profiled = events[events["previous_profile_day"].notna()]
    slip = config.execution.slippage_per_side_pips * pip_size
    stress_delta = 2 * (
        config.execution.stress_slippage_per_side_pips
        - config.execution.slippage_per_side_pips
    )
    opened = pd.to_datetime(trades["event_timestamp_utc"], utc=True)
    signals = pd.to_datetime(trades["signal_timestamp_utc"], utc=True)
    entries = pd.to_datetime(trades["entry_timestamp_utc"], utc=True)
    exits = pd.to_datetime(trades["exit_timestamp_utc"], utc=True)
    expected_entry = np.where(
        trades["direction"] == 1,
        trades["entry_ask_open"] + slip,
        trades["entry_bid_open"] - slip,
    )
    expected_stop = np.where(
        trades["direction"] == 1,
        trades["known_excursion_price"]
        - config.execution.stop_buffer_pips * pip_size,
        trades["known_excursion_price"]
        + config.execution.stop_buffer_pips * pip_size,
    )
    expected_pnl = (
        trades["direction"]
        * (trades["exit_price"] - trades["entry_price"])
        / pip_size
    )
    stop_exit = np.where(
        trades["direction"] == 1,
        trades["stop_price"] - slip,
        trades["stop_price"] + slip,
    )
    expected_risk = (
        trades["direction"] * (trades["entry_price"] - stop_exit) / pip_size
    )
    checks = {
        "event_rows_unique_within_sample": bool(
            ~events.duplicated(["sample_role", "event_id"]).any()
        ),
        "profile_days_strictly_prior": bool(
            (
                pd.to_datetime(profiled["previous_profile_day"])
                < pd.to_datetime(profiled["fx_trading_day"])
            ).all()
        ),
        "candidate_direction_matches_value_state": bool(
            (
                ((candidates["value_state"] == "above_value")
                 & (candidates["direction"] == -1))
                | ((candidates["value_state"] == "below_value")
                   & (candidates["direction"] == 1))
            ).all()
        ),
        "signal_entry_timing": bool(
            (
                (signals >= opened)
                & (signals < opened + pd.Timedelta(
                    minutes=config.execution.entry_deadline_minutes
                ))
                & (entries == signals + pd.Timedelta(minutes=5))
                & (entries <= opened + pd.Timedelta(
                    minutes=config.execution.entry_deadline_minutes
                ))
            ).all()
        ),
        "exit_timing": bool(
            (
                (exits > entries)
                & (exits <= opened + pd.Timedelta(
                    minutes=config.execution.timeout_minutes
                ))
            ).all()
        ),
        "entry_side_and_slippage": bool(
            np.allclose(trades["entry_price"], expected_entry)
        ),
        "stop_uses_known_excursion": bool(
            np.allclose(trades["stop_price"], expected_stop)
        ),
        "target_is_favorable": bool(
            (
                trades["direction"]
                * (trades["target_price"] - trades["entry_price"])
                > 0
            ).all()
        ),
        "pnl_arithmetic": bool(np.allclose(trades["pnl_pips"], expected_pnl)),
        "risk_arithmetic": bool(
            np.allclose(trades["initial_risk_pips"], expected_risk)
            and (trades["initial_risk_pips"] > 0).all()
        ),
        "r_arithmetic": bool(
            np.allclose(
                trades["r_multiple"],
                trades["pnl_pips"] / trades["initial_risk_pips"],
            )
        ),
        "stressed_r_arithmetic": bool(
            np.allclose(
                trades["stressed_r_multiple"],
                (trades["pnl_pips"] - stress_delta)
                / trades["initial_risk_pips"],
            )
        ),
        "maximum_one_trade_per_event": bool(
            ~trades.duplicated(["sample_role", "event_id"]).any()
        ),
    }
    return {"passed": all(checks.values()), "checks": checks}


def evaluate_validation_gate(
    statistics: pd.DataFrame,
    validation_events: pd.DataFrame,
    validation_upstream: dict[str, Any],
    invariants: dict[str, object],
    config: OpeningValueStrategyConfig,
) -> dict[str, object]:
    """Evaluate only the untouched New York validation sample."""

    primary = statistics[
        statistics["sample_role"].eq("validation")
        & statistics["session_name"].eq(config.analysis.primary_session)
    ]
    if len(primary) != 1:
        raise ValueError("Validation statistics must contain one primary session row")
    row = primary.iloc[0]
    eligible = validation_events["phase1_eligible"].sum()
    coverage = (
        float(validation_events["value_eligible"].sum() / eligible)
        if eligible
        else 0.0
    )
    phase1_gate = validation_upstream["phase1"]["research_gate"]
    checks = {
        "execution_invariants": bool(invariants["passed"]),
        "phase1_source_opening_effect_gate": bool(
            phase1_gate["development_passed"]
        ),
        "phase2_data_quality": bool(
            validation_upstream["phase2"]["data_quality_valid"]
        ),
        "value_feature_coverage": (
            coverage >= config.analysis.minimum_value_feature_coverage_ratio
        ),
        "minimum_trades": row["trades"] >= config.analysis.minimum_trades,
        "minimum_active_months": (
            row["active_months"] >= config.analysis.minimum_active_months
        ),
        "minimum_long_trades": (
            row["long_trades"] >= config.analysis.minimum_trades_per_direction
        ),
        "minimum_short_trades": (
            row["short_trades"] >= config.analysis.minimum_trades_per_direction
        ),
        "minimum_expectancy_r": (
            row["mean_r"] >= config.analysis.minimum_expectancy_r
        ),
        "cluster_interval_strictly_above_zero": row["mean_r_ci_low"] > 0,
        "minimum_profit_factor": (
            row["profit_factor"] >= config.analysis.minimum_profit_factor
        ),
        "maximum_drawdown": (
            row["maximum_drawdown_r"] <= config.analysis.maximum_drawdown_r
        ),
        "positive_stressed_expectancy": row["mean_stressed_r"] > 0,
    }
    return {
        "passed": all(checks.values()),
        "decision": "PASS" if all(checks.values()) else "FAIL",
        "authoritative_sample": "2025 new_york",
        "value_feature_coverage_ratio": coverage,
        "checks": {name: bool(value) for name, value in checks.items()},
    }


def _create_figures(
    events: pd.DataFrame, monthly: pd.DataFrame, output: Path
) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output.mkdir(parents=True, exist_ok=True)
    paths = []
    for sample_role in ("development", "validation"):
        figure, axis = plt.subplots(figsize=(9, 4.5))
        sample = events[
            events["sample_role"].eq(sample_role) & events["trade_executed"]
        ]
        for session_name, trades in sample.groupby(
            "session_name", observed=True, sort=True
        ):
            trades = trades.sort_values("entry_timestamp_utc", kind="stable")
            axis.plot(
                range(1, len(trades) + 1),
                trades["r_multiple"].cumsum(),
                label=session_name,
            )
        axis.axhline(0, color="black", linewidth=0.8)
        axis.set(
            title=f"{sample_role.title()} cumulative R",
            xlabel="Trade",
            ylabel="R",
        )
        if not sample.empty:
            axis.legend()
        figure.tight_layout()
        path = output / f"{sample_role}_cumulative_r.png"
        figure.savefig(path, dpi=150)
        plt.close(figure)
        paths.append(path)

    figure, axis = plt.subplots(figsize=(10, 4.5))
    if not monthly.empty:
        for keys, frame in monthly.groupby(
            ["sample_role", "session_name"], observed=True, sort=True
        ):
            axis.plot(
                frame["entry_month"],
                frame["net_r"],
                marker="o",
                label=" / ".join(keys),
            )
    axis.axhline(0, color="black", linewidth=0.8)
    axis.tick_params(axis="x", rotation=45)
    axis.set(title="Monthly net R", xlabel="Entry month", ylabel="R")
    if not monthly.empty:
        axis.legend()
    figure.tight_layout()
    path = output / "monthly_net_r.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    paths.append(path)
    return paths


def _render_report(
    statistics: pd.DataFrame,
    gate: dict[str, object],
    development: ProjectConfig,
    validation: ProjectConfig,
) -> str:
    columns = [
        "sample_role",
        "session_name",
        "trades",
        "long_trades",
        "short_trades",
        "active_months",
        "mean_r",
        "mean_r_ci_low",
        "mean_r_ci_high",
        "profit_factor",
        "maximum_drawdown_r",
        "mean_stressed_r",
    ]
    display = statistics[columns].round(4).fillna("NA")
    headings = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(str(value) for value in row) + " |"
        for row in display.itertuples(index=False, name=None)
    ]
    table = "\n".join([headings, separator, *body])
    checks = "\n".join(
        f"- [{'x' if passed else ' '}] `{name}`"
        for name, passed in gate["checks"].items()
    )
    return f"""# Phase 4 — Opening-Only Volume Profile Validation

**Authoritative decision: {gate['decision']}**

- Development: `{development.research.data.start}` to
  `{development.research.data.end}` (end exclusive)
- Untouched validation: `{validation.research.data.start}` to
  `{validation.research.data.end}` (end exclusive)
- Primary hypothesis: 2025 New York outside-value reversion
- London is a replication only and cannot rescue the decision.

## Performance

{table}

## Validation gate

{checks}

Value-feature coverage: `{gate['value_feature_coverage_ratio']:.2%}`.

The decision uses the frozen strategy and cost model. A failed check is not
permission to tune on 2025; any new rule requires a separately registered phase
and a new untouched sample.
"""


def run_phase4(
    project_root: Path,
    development: ProjectConfig,
    validation: ProjectConfig,
    value_config: ValueStateConfig,
    strategy_config: OpeningValueStrategyConfig,
) -> dict[str, Any]:
    """Run frozen 2024 development context and untouched 2025 validation."""

    dev_interval = (
        development.research.data.start.isoformat(),
        development.research.data.end.isoformat(),
    )
    val_interval = (
        validation.research.data.start.isoformat(),
        validation.research.data.end.isoformat(),
    )
    if dev_interval != ("2024-01-01", "2025-01-01"):
        raise ValueError("Phase-4 development interval must remain frozen to 2024")
    if val_interval != ("2025-01-01", "2026-01-01"):
        raise ValueError("Phase-4 validation interval must remain frozen to 2025")
    if development.sessions != validation.sessions:
        raise ValueError("Development and validation session contracts must match")
    if (
        development.research.instrument
        != validation.research.instrument
    ):
        raise ValueError("Development and validation instruments must match")
    required_sessions = {
        strategy_config.analysis.primary_session,
        strategy_config.analysis.replication_session,
    }
    if not required_sessions.issubset(development.sessions.sessions):
        raise ValueError("Phase-4 strategy sessions are missing from session config")

    dev_events, dev_upstream = _load_upstream(
        project_root, development, value_config
    )
    val_events, val_upstream = _load_upstream(
        project_root, validation, value_config
    )
    dev_bars = load_m5_range(
        project_root,
        development,
        development.research.data.start,
        development.research.data.end,
    )
    val_bars = load_m5_range(
        project_root,
        validation,
        validation.research.data.start,
        validation.research.data.end,
    )
    dev_results = simulate_opening_value_strategy(
        dev_events,
        dev_bars,
        strategy_config,
        pip_size=development.research.instrument.pip_size,
        sample_role="development",
    )
    val_results = simulate_opening_value_strategy(
        val_events,
        val_bars,
        strategy_config,
        pip_size=validation.research.instrument.pip_size,
        sample_role="validation",
    )
    all_events = pd.concat([dev_results, val_results], ignore_index=True)
    statistics = performance_statistics(
        all_events,
        strategy_config,
        random_seed=development.research.study.random_seed,
    )
    funnel = _event_funnel(all_events)
    monthly = _monthly_statistics(all_events)
    exclusions = _exclusion_statistics(all_events)
    invariants = execution_invariants(
        all_events,
        strategy_config,
        pip_size=development.research.instrument.pip_size,
    )
    gate = evaluate_validation_gate(
        statistics,
        val_results,
        val_upstream,
        invariants,
        strategy_config,
    )

    run_id = phase4_run_id(
        development, validation, value_config, strategy_config
    )
    processed = resolve_within_project(
        project_root, development.research.data.paths.processed
    )
    output = processed / "reports" / "phase4" / run_id
    output.mkdir(parents=True, exist_ok=True)
    dev_results.to_parquet(output / "development_events.parquet", index=False)
    val_results.to_parquet(output / "validation_events.parquet", index=False)
    dev_results[dev_results["trade_executed"]].to_parquet(
        output / "development_trades.parquet", index=False
    )
    val_results[val_results["trade_executed"]].to_parquet(
        output / "validation_trades.parquet", index=False
    )
    funnel.to_csv(output / "event_funnel.csv", index=False)
    statistics.to_csv(output / "performance_statistics.csv", index=False)
    monthly.to_csv(output / "monthly_statistics.csv", index=False)
    exclusions.to_csv(output / "exclusion_statistics.csv", index=False)
    upstream_quality = all(
        item[phase]["data_quality_valid"]
        for item in (dev_upstream, val_upstream)
        for phase in ("phase1", "phase2")
    )
    data_quality = {
        "valid": bool(invariants["passed"] and upstream_quality),
        "execution_invariants": invariants,
        "development_upstream": dev_upstream,
        "validation_upstream": val_upstream,
        "validation_gate": gate,
    }
    _write_json(data_quality, output / "data_quality.json")
    figures = _create_figures(all_events, monthly, output / "figures")
    report = _render_report(statistics, gate, development, validation)
    (output / "report.md").write_text(report, encoding="utf-8")

    artifacts = sorted(
        path for path in output.rglob("*") if path.is_file()
    )
    combined_config = {
        "development": development.model_dump(mode="json"),
        "validation": validation.model_dump(mode="json"),
        "value_state": value_config.model_dump(mode="json"),
        "opening_value": strategy_config.model_dump(mode="json"),
    }
    manifest = {
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "config": combined_config,
        "config_sha256": _hash_json(combined_config),
        "git": _git_state(project_root),
        "inputs": {
            "development": dev_upstream,
            "validation": val_upstream,
        },
        "validation_gate": gate,
        "runtime": {
            "python": platform.python_version(),
            "packages": {
                package: importlib.metadata.version(package)
                for package in ("numpy", "pandas", "pyarrow", "matplotlib")
            },
        },
        "rows": {
            "development_events": len(dev_results),
            "development_trades": int(dev_results["trade_executed"].sum()),
            "validation_events": len(val_results),
            "validation_trades": int(val_results["trade_executed"].sum()),
            "performance_statistics": len(statistics),
            "monthly_statistics": len(monthly),
            "exclusion_statistics": len(exclusions),
        },
        "artifacts": [
            {
                "path": str(path.relative_to(output)),
                "sha256": _hash_file(path),
            }
            for path in artifacts
        ],
        "figures": [str(path.relative_to(output)) for path in figures],
    }
    _write_json(manifest, output / "run_manifest.json")
    return {
        **manifest,
        "output_directory": str(output.relative_to(project_root)),
        "report": str((output / "report.md").relative_to(project_root)),
    }
