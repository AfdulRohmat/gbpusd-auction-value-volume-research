"""Orchestration and reporting for the Phase-7 auction-state taxonomy."""

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
from scipy.stats import norm

from gbpusd_research.config import AuctionTaxonomyConfig, ProjectConfig
from gbpusd_research.data.pipeline import load_m5_range
from gbpusd_research.features.sessions import build_session_calendar
from gbpusd_research.research.auction_state_taxonomy import (
    STABLE_STATES,
    build_state_episodes,
    build_state_timeline,
    build_state_transitions,
)
from gbpusd_research.research.controls import (
    build_fixed_control_calendar,
    build_matched_control_calendar,
)
from gbpusd_research.research.phase4 import _artifact_records, _git_state
from gbpusd_research.utils.paths import resolve_within_project


def _hash_json(content: object) -> str:
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, default=str).encode()
    ).hexdigest()


def _write_json(content: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(content, indent=2, sort_keys=True), encoding="utf-8")


def phase7_run_id(
    first: ProjectConfig,
    second: ProjectConfig,
    taxonomy: AuctionTaxonomyConfig,
) -> str:
    content = {
        "first": first.model_dump(mode="json"),
        "second": second.model_dump(mode="json"),
        "taxonomy": taxonomy.model_dump(mode="json"),
    }
    return (
        f"{first.research.data.start:%Y%m%d}_"
        f"{second.research.data.end:%Y%m%d}_{_hash_json(content)[:8]}"
    )


def episode_statistics(episodes: pd.DataFrame) -> pd.DataFrame:
    grouped = episodes.groupby(
        ["sample_year", "state", "dominant_activity_regime"],
        observed=True,
        sort=True,
    )
    return (
        grouped.agg(
            episodes=("episode_id", "size"),
            median_duration_minutes=("duration_minutes", "median"),
            mean_duration_minutes=("duration_minutes", "mean"),
            duration_q25_minutes=(
                "duration_minutes",
                lambda value: value.quantile(0.25),
            ),
            duration_q75_minutes=(
                "duration_minutes",
                lambda value: value.quantile(0.75),
            ),
            median_width_pips=("episode_width_pips", "median"),
            right_censored=("right_censored", "sum"),
        )
        .reset_index()
        .assign(
            right_censored_share=lambda value: (
                value["right_censored"] / value["episodes"]
            )
        )
    )


def transition_matrix(
    episodes: pd.DataFrame,
    transitions: pd.DataFrame,
) -> pd.DataFrame:
    counts = (
        transitions.groupby(
            ["sample_year", "from_state", "to_state"],
            observed=True,
            sort=True,
        )
        .size()
        .rename("transitions")
        .reset_index()
    )
    totals = counts.groupby(
        ["sample_year", "from_state"], observed=True, sort=True
    )["transitions"].transform("sum")
    counts["conditional_probability"] = counts["transitions"] / totals
    episode_counts = (
        episodes.groupby(["sample_year", "state"], observed=True, sort=True)
        .size()
        .rename("from_state_episodes")
        .reset_index()
        .rename(columns={"state": "from_state"})
    )
    return counts.merge(
        episode_counts,
        on=["sample_year", "from_state"],
        how="left",
        validate="many_to_one",
    )


def balance_hazard(
    episodes: pd.DataFrame,
    transitions: pd.DataFrame,
    config: AuctionTaxonomyConfig,
) -> pd.DataFrame:
    balance = episodes[episodes["state"].eq("balance")].copy()
    next_lookup = transitions.set_index("from_episode_id")["to_state"].to_dict()
    balance["next_state"] = balance["episode_id"].map(next_lookup)
    balance["observed_imbalance"] = balance["next_state"].isin(
        ["imbalance_up", "imbalance_down"]
    )
    bins = config.transition.balance_age_bins_minutes
    rows = []
    for year, frame in balance.groupby("sample_year", observed=True, sort=True):
        for index, lower in enumerate(bins):
            upper = bins[index + 1] if index + 1 < len(bins) else np.inf
            at_risk = frame[frame["duration_minutes"].gt(lower)]
            events = at_risk[
                at_risk["observed_imbalance"]
                & at_risk["duration_minutes"].le(upper)
            ]
            if np.isfinite(upper):
                exposure = np.minimum(
                    at_risk["duration_minutes"] - lower,
                    upper - lower,
                ).clip(lower=0)
            else:
                exposure = (at_risk["duration_minutes"] - lower).clip(lower=0)
            exposure_minutes = float(exposure.sum())
            rows.append(
                {
                    "sample_year": int(year),
                    "age_bin_start_minutes": lower,
                    "age_bin_end_minutes": upper,
                    "age_bin": (
                        f"{lower}-{int(upper) if np.isfinite(upper) else 'plus'}"
                    ),
                    "at_risk": len(at_risk),
                    "balance_to_imbalance_transitions": len(events),
                    "exposure_minutes": exposure_minutes,
                    "conditional_transition_probability": (
                        len(events) / len(at_risk) if len(at_risk) else np.nan
                    ),
                    "transition_rate_per_30m_exposure": (
                        len(events) * 30 / exposure_minutes
                        if exposure_minutes > 0
                        else np.nan
                    ),
                }
            )
    return pd.DataFrame(rows)


def transitions_by_clock(
    transitions: pd.DataFrame,
    timeline: pd.DataFrame,
) -> pd.DataFrame:
    primary = transitions[
        transitions["from_state"].eq("balance")
        & transitions["to_state"].isin(["imbalance_up", "imbalance_down"])
    ]
    rows = []
    for timezone_name, column in (
        ("Europe/London", "london_local_hour"),
        ("America/New_York", "new_york_local_hour"),
    ):
        grouped = primary.groupby(
            ["sample_year", column], observed=True, sort=True
        ).size()
        exposure = (
            timeline[timeline["observable_state"].eq("balance")]
            .groupby(["sample_year", column], observed=True, sort=True)
            .size()
        )
        keys = sorted(set(grouped.index) | set(exposure.index))
        for year, hour in keys:
            count = int(grouped.get((year, hour), 0))
            balance_windows = int(exposure.get((year, hour), 0))
            balance_hours = balance_windows * 5 / 60
            rows.append(
                {
                    "sample_year": int(year),
                    "clock_timezone": timezone_name,
                    "local_hour": int(hour),
                    "transitions": count,
                    "balance_exposure_hours": balance_hours,
                    "transitions_per_100_balance_hours": (
                        count * 100 / balance_hours if balance_hours else np.nan
                    ),
                }
            )
    return pd.DataFrame(rows)


def state_occupancy(timeline: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for view, column in (
        ("raw", "raw_auction_state"),
        ("observable", "observable_state"),
    ):
        eligible = timeline[timeline[column].notna()]
        for (year, state), count in eligible.groupby(
            ["sample_year", column], observed=True, sort=True
        ).size().items():
            denominator = int(eligible["sample_year"].eq(year).sum())
            rows.append(
                {
                    "sample_year": int(year),
                    "state_view": view,
                    "state": state,
                    "windows": int(count),
                    "minutes": int(count) * 5,
                    "share": count / denominator,
                }
            )
    return pd.DataFrame(rows)


def transition_signatures(transitions: pd.DataFrame) -> pd.DataFrame:
    primary = transitions[
        transitions["from_state"].eq("balance")
        & transitions["to_state"].isin(["imbalance_up", "imbalance_down"])
    ]
    counts = (
        primary.groupby(
            ["sample_year", "to_state", "signature"],
            observed=True,
            sort=True,
        )
        .size()
        .rename("transitions")
        .reset_index()
    )
    totals = counts.groupby(
        ["sample_year", "to_state"], observed=True, sort=True
    )["transitions"].transform("sum")
    counts["share"] = counts["transitions"] / totals
    return counts


def transition_antecedents(transitions: pd.DataFrame) -> pd.DataFrame:
    primary = transitions[
        transitions["from_state"].eq("balance")
        & transitions["to_state"].isin(["imbalance_up", "imbalance_down"])
    ].copy()
    primary["directional_boundary_tests"] = np.where(
        primary["to_state"].eq("imbalance_up"),
        primary["upper_boundary_tests"],
        primary["lower_boundary_tests"],
    )
    primary["opposite_boundary_tests"] = np.where(
        primary["to_state"].eq("imbalance_up"),
        primary["lower_boundary_tests"],
        primary["upper_boundary_tests"],
    )
    primary["directional_side_more_tested"] = (
        primary["directional_boundary_tests"]
        > primary["opposite_boundary_tests"]
    )
    return (
        primary.groupby(
            ["sample_year", "to_state"], observed=True, sort=True
        )
        .agg(
            transitions=("transition_id", "size"),
            median_prior_duration_minutes=(
                "prior_episode_duration_minutes",
                "median",
            ),
            median_prior_width_pips=("prior_episode_width_pips", "median"),
            boundary_break_share=("boundary_break", "mean"),
            activity_burst_share=("activity_burst", "mean"),
            opening_catalyst_window_share=("opening_catalyst_window", "mean"),
            median_directional_boundary_tests=(
                "directional_boundary_tests",
                "median",
            ),
            median_opposite_boundary_tests=("opposite_boundary_tests", "median"),
            directional_side_more_tested_share=(
                "directional_side_more_tested",
                "mean",
            ),
        )
        .reset_index()
    )


def _calendar_events(
    bars: pd.DataFrame,
    project: ProjectConfig,
    taxonomy: AuctionTaxonomyConfig,
    *,
    sample_year: int,
) -> pd.DataFrame:
    start = pd.Timestamp(project.research.data.start, tz="UTC").to_pydatetime()
    end = pd.Timestamp(project.research.data.end, tz="UTC").to_pydatetime()
    openings = build_session_calendar(start, end, project.sessions)
    fixed = build_fixed_control_calendar(openings, project.sessions)
    matched = build_matched_control_calendar(
        bars,
        openings,
        project.sessions,
        preopen_minutes=taxonomy.state.window_minutes,
        forward_minutes=max(taxonomy.transition.horizons_minutes),
        random_seed=project.research.study.random_seed,
    )
    frames = []
    for event_kind, calendar in (
        ("session_open", openings),
        ("fixed_control", fixed),
        ("matched_control", matched),
    ):
        frame = calendar[
            ["session_name", "local_session_date", "open_timestamp_utc"]
        ].copy()
        frame["event_kind"] = event_kind
        frame["sample_year"] = sample_year
        frame["calendar_event_id"] = (
            event_kind
            + ":"
            + frame["session_name"].astype(str)
            + ":"
            + frame["local_session_date"].astype(str)
        )
        frames.append(frame)
    return pd.concat(frames, ignore_index=True).rename(
        columns={"open_timestamp_utc": "event_timestamp_utc"}
    )


def build_opening_control_events(
    timeline: pd.DataFrame,
    transitions: pd.DataFrame,
    bars: pd.DataFrame,
    project: ProjectConfig,
    taxonomy: AuctionTaxonomyConfig,
    *,
    sample_year: int,
) -> pd.DataFrame:
    events = _calendar_events(
        bars, project, taxonomy, sample_year=sample_year
    ).sort_values("event_timestamp_utc", kind="stable")
    state = timeline[
        [
            "available_at",
            "observable_state",
            "observable_episode_id",
            "segment_id",
        ]
    ].sort_values("available_at", kind="stable")
    merged = pd.merge_asof(
        events,
        state,
        left_on="event_timestamp_utc",
        right_on="available_at",
        direction="backward",
        tolerance=pd.Timedelta(5, unit="min"),
    )
    merged["state_staleness_minutes"] = (
        merged["event_timestamp_utc"] - merged["available_at"]
    ).dt.total_seconds() / 60
    primary = transitions[
        transitions["from_state"].eq("balance")
        & transitions["to_state"].isin(["imbalance_up", "imbalance_down"])
    ].copy()
    lookup = {
        episode_id: group.sort_values("confirmed_at", kind="stable")
        for episode_id, group in primary.groupby("from_episode_id", sort=False)
    }
    first_ids = []
    first_times = []
    first_directions = []
    for event in merged.itertuples(index=False):
        candidates = lookup.get(event.observable_episode_id)
        if candidates is None or event.observable_state != "balance":
            first_ids.append(None)
            first_times.append(pd.NaT)
            first_directions.append(None)
            continue
        candidates = candidates[
            pd.to_datetime(candidates["confirmed_at"], utc=True).gt(
                pd.Timestamp(event.event_timestamp_utc)
            )
        ]
        if candidates.empty:
            first_ids.append(None)
            first_times.append(pd.NaT)
            first_directions.append(None)
            continue
        first = candidates.iloc[0]
        first_ids.append(first["transition_id"])
        first_times.append(first["confirmed_at"])
        first_directions.append(first["to_state"])
    merged["first_transition_id"] = first_ids
    merged["first_transition_confirmed_at"] = pd.to_datetime(
        first_times, utc=True
    )
    merged["first_transition_direction"] = first_directions
    for horizon in taxonomy.transition.horizons_minutes:
        merged[f"transition_within_{horizon}"] = (
            merged["first_transition_confirmed_at"].notna()
            & merged["first_transition_confirmed_at"].le(
                merged["event_timestamp_utc"] + np.timedelta64(horizon, "m")
            )
        )
    return merged.sort_values(
        ["sample_year", "event_kind", "session_name", "local_session_date"],
        kind="stable",
    ).reset_index(drop=True)


def _wilson_interval(
    successes: int,
    total: int,
    confidence_level: float,
) -> tuple[float, float]:
    if total == 0:
        return np.nan, np.nan
    z = float(norm.ppf(1 - (1 - confidence_level) / 2))
    proportion = successes / total
    denominator = 1 + z**2 / total
    center = (proportion + z**2 / (2 * total)) / denominator
    radius = (
        z
        * np.sqrt(
            proportion * (1 - proportion) / total + z**2 / (4 * total**2)
        )
        / denominator
    )
    return center - radius, center + radius


def opening_control_statistics(
    events: pd.DataFrame,
    taxonomy: AuctionTaxonomyConfig,
) -> pd.DataFrame:
    rows = []
    grouped = events.groupby(
        ["sample_year", "session_name", "event_kind"],
        observed=True,
        sort=True,
    )
    for keys, frame in grouped:
        year, session, event_kind = keys
        balance = frame[frame["observable_state"].eq("balance")]
        for horizon in taxonomy.transition.horizons_minutes:
            successes = int(balance[f"transition_within_{horizon}"].sum())
            low, high = _wilson_interval(
                successes,
                len(balance),
                taxonomy.analysis.confidence_level,
            )
            rows.append(
                {
                    "sample_year": int(year),
                    "session_name": session,
                    "event_kind": event_kind,
                    "horizon_minutes": horizon,
                    "scheduled_events": len(frame),
                    "state_available_events": int(
                        frame["observable_state"].notna().sum()
                    ),
                    "balance_at_start": len(balance),
                    "balance_to_imbalance": successes,
                    "transition_probability": (
                        successes / len(balance) if len(balance) else np.nan
                    ),
                    "probability_ci_low": low,
                    "probability_ci_high": high,
                }
            )
    return pd.DataFrame(rows)


def opening_control_differences(statistics: pd.DataFrame) -> pd.DataFrame:
    pivot = statistics.pivot_table(
        index=["sample_year", "session_name", "horizon_minutes"],
        columns="event_kind",
        values="transition_probability",
        aggfunc="first",
    ).reset_index()
    pivot["opening_minus_fixed"] = (
        pivot["session_open"] - pivot["fixed_control"]
    )
    pivot["opening_minus_matched"] = (
        pivot["session_open"] - pivot["matched_control"]
    )
    return pivot


def execution_invariants(
    timeline: pd.DataFrame,
    episodes: pd.DataFrame,
    transitions: pd.DataFrame,
    events: pd.DataFrame,
    config: AuctionTaxonomyConfig,
) -> dict[str, object]:
    available = pd.to_datetime(timeline["available_at"], utc=True)
    timestamps = pd.to_datetime(timeline["timestamp"], utc=True)
    overlap_ok = True
    adjacency_ok = True
    for _, frame in episodes.groupby("segment_id", sort=True):
        ordered = frame.sort_values("start_at", kind="stable")
        if len(ordered) > 1:
            overlap_ok &= bool(
                (
                    pd.to_datetime(ordered["end_at"].iloc[:-1]).to_numpy()
                    <= pd.to_datetime(ordered["start_at"].iloc[1:]).to_numpy()
                ).all()
            )
    episode_lookup = episodes.set_index("episode_id")
    for transition in transitions.itertuples(index=False):
        previous = episode_lookup.loc[transition.from_episode_id]
        current = episode_lookup.loc[transition.to_episode_id]
        adjacency_ok &= bool(
            previous["segment_id"] == current["segment_id"]
            and pd.Timestamp(previous["end_at"]) == pd.Timestamp(current["start_at"])
        )
    valid_events = events[events["available_at"].notna()]
    checks = {
        "timeline_timestamp_unique": bool(~timeline["timestamp"].duplicated().any()),
        "feature_availability_after_bar_close": bool(
            (available == timestamps + np.timedelta64(5, "m")).all()
        ),
        "raw_states_registered": bool(
            set(timeline["raw_auction_state"])
            <= STABLE_STATES | {"transition", "warmup"}
        ),
        "live_states_registered": bool(
            set(timeline["observable_state"].dropna()) <= STABLE_STATES
        ),
        "episode_keys_unique": bool(~episodes["episode_id"].duplicated().any()),
        "transition_keys_unique": bool(
            ~transitions["transition_id"].duplicated().any()
        ),
        "episodes_non_overlapping": overlap_ok,
        "transitions_connect_adjacent_episodes": adjacency_ok,
        "confirmation_latency_exact": bool(
            (
                episodes["confirmation_latency_minutes"]
                == (config.state.confirmation_windows - 1) * 5
            ).all()
        ),
        "confirmations_not_before_episode_start": bool(
            (
                pd.to_datetime(episodes["confirmed_at"], utc=True)
                >= pd.to_datetime(episodes["start_at"], utc=True)
            ).all()
        ),
        "event_state_point_in_time": bool(
            (
                pd.to_datetime(valid_events["available_at"], utc=True)
                <= pd.to_datetime(valid_events["event_timestamp_utc"], utc=True)
            ).all()
        ),
        "event_state_not_stale": bool(
            valid_events["state_staleness_minutes"].between(0, 5).all()
        ),
        "event_keys_unique": bool(~events["calendar_event_id"].duplicated().any()),
        "all_control_kinds_present": bool(
            set(events["event_kind"])
            == {"session_open", "fixed_control", "matched_control"}
        ),
    }
    return {"passed": bool(all(checks.values())), "checks": checks}


def _create_figures(
    episodes: pd.DataFrame,
    clocks: pd.DataFrame,
    opening_stats: pd.DataFrame,
    output: Path,
) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    duration = (
        episodes.groupby(["sample_year", "state"], observed=True, sort=True)[
            "duration_minutes"
        ]
        .median()
        .unstack(0)
    )
    figure, axis = plt.subplots(figsize=(9, 5))
    duration.plot(kind="bar", ax=axis)
    axis.set(title="Median confirmed-state episode duration", ylabel="Minutes")
    axis.tick_params(axis="x", rotation=20)
    figure.tight_layout()
    path = output / "episode_duration.png"
    figure.savefig(path, dpi=160)
    plt.close(figure)
    paths.append(path)

    london = clocks[clocks["clock_timezone"].eq("Europe/London")]
    pivot = london.pivot(
        index="local_hour",
        columns="sample_year",
        values="transitions_per_100_balance_hours",
    )
    figure, axis = plt.subplots(figsize=(10, 5))
    pivot.plot(kind="bar", ax=axis)
    axis.set(
        title="Balance-to-imbalance transitions by London-local hour",
        xlabel="Hour",
        ylabel="Transitions per 100 balance-hours",
    )
    figure.tight_layout()
    path = output / "transitions_by_london_hour.png"
    figure.savefig(path, dpi=160)
    plt.close(figure)
    paths.append(path)

    view = opening_stats[opening_stats["horizon_minutes"].eq(60)].copy()
    labels = [
        f"{row.sample_year} {row.session_name}\n{row.event_kind}"
        for row in view.itertuples()
    ]
    figure, axis = plt.subplots(figsize=(12, 5))
    axis.bar(labels, view["transition_probability"])
    axis.set(
        title="P(balance to imbalance within 60m | balance at event)",
        ylabel="Probability",
    )
    axis.tick_params(axis="x", rotation=45)
    figure.tight_layout()
    path = output / "opening_control_probability_60m.png"
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
    occupancy: pd.DataFrame,
    episodes: pd.DataFrame,
    episode_stats: pd.DataFrame,
    matrix: pd.DataFrame,
    hazard: pd.DataFrame,
    signatures: pd.DataFrame,
    antecedents: pd.DataFrame,
    opening_stats: pd.DataFrame,
    opening_differences: pd.DataFrame,
    invariants: dict[str, object],
) -> str:
    state_summary = (
        episodes.groupby(["sample_year", "state"], observed=True, sort=True)
        .agg(
            episodes=("episode_id", "size"),
            median_duration_minutes=("duration_minutes", "median"),
            duration_q25_minutes=(
                "duration_minutes",
                lambda value: value.quantile(0.25),
            ),
            duration_q75_minutes=(
                "duration_minutes",
                lambda value: value.quantile(0.75),
            ),
            median_width_pips=("episode_width_pips", "median"),
        )
        .reset_index()
    )
    opening_60 = opening_stats[opening_stats["horizon_minutes"].eq(60)][
        [
            "sample_year",
            "session_name",
            "event_kind",
            "balance_at_start",
            "balance_to_imbalance",
            "transition_probability",
            "probability_ci_low",
            "probability_ci_high",
        ]
    ]
    differences_60 = opening_differences[
        opening_differences["horizon_minutes"].eq(60)
    ]
    observable_occupancy = occupancy[occupancy["state_view"].eq("observable")]
    activity = episode_stats[
        [
            "sample_year",
            "state",
            "dominant_activity_regime",
            "episodes",
            "median_duration_minutes",
            "median_width_pips",
        ]
    ]
    return f"""# Phase 7 Auction-State Taxonomy Report

**Research status:** exploratory taxonomy; no trading or validation decision
**Execution invariants:** {"PASS" if invariants["passed"] else "FAIL"}

## State episodes

{_markdown_table(state_summary)}

## Observable state occupancy

{_markdown_table(observable_occupancy)}

## State and activity are separate

{_markdown_table(activity)}

## Transition matrix

{_markdown_table(matrix)}

## Balance-age transition hazard

{_markdown_table(hazard)}

## Balance-to-imbalance signatures

{_markdown_table(signatures)}

## Balance-to-imbalance antecedents

{_markdown_table(antecedents)}

## Opening versus controls: 60-minute horizon

{_markdown_table(opening_60)}

Opening-minus-control differences at 60 minutes:

{_markdown_table(differences_60)}

The conditional denominator is events whose last point-in-time confirmed state
was balance at the event timestamp. A transition is counted only after its
registered two-window confirmation becomes observable.

## Interpretation boundary

The taxonomy describes recurring price geometry and associated transition
signatures. It does not prove that a clock event or measured antecedent caused
the transition, and it contains no entry or profitability claim.
"""


def run_phase7(
    project_root: Path,
    first: ProjectConfig,
    second: ProjectConfig,
    taxonomy: AuctionTaxonomyConfig,
) -> dict[str, Any]:
    intervals = (
        (first.research.data.start.isoformat(), first.research.data.end.isoformat()),
        (second.research.data.start.isoformat(), second.research.data.end.isoformat()),
    )
    if intervals != (
        ("2024-01-01", "2025-01-01"),
        ("2025-01-01", "2026-01-01"),
    ):
        raise ValueError("Phase-7 evidence intervals must remain 2024 and 2025")
    if first.sessions != second.sessions:
        raise ValueError("Phase-7 session contracts must match")
    if first.research.instrument != second.research.instrument:
        raise ValueError("Phase-7 instruments must match")

    timelines = []
    episodes_all = []
    transitions_all = []
    events_all = []
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
        events = build_opening_control_events(
            timeline,
            transitions,
            bars,
            project,
            taxonomy,
            sample_year=year,
        )
        timelines.append(timeline)
        episodes_all.append(episodes)
        transitions_all.append(transitions)
        events_all.append(events)

    timeline = pd.concat(timelines, ignore_index=True)
    episodes = pd.concat(episodes_all, ignore_index=True)
    transitions = pd.concat(transitions_all, ignore_index=True)
    events = pd.concat(events_all, ignore_index=True)
    episode_stats = episode_statistics(episodes)
    occupancy = state_occupancy(timeline)
    matrix = transition_matrix(episodes, transitions)
    hazard = balance_hazard(episodes, transitions, taxonomy)
    clocks = transitions_by_clock(transitions, timeline)
    signatures = transition_signatures(transitions)
    antecedents = transition_antecedents(transitions)
    opening_stats = opening_control_statistics(events, taxonomy)
    opening_differences = opening_control_differences(opening_stats)
    invariants = execution_invariants(
        timeline, episodes, transitions, events, taxonomy
    )

    run_id = phase7_run_id(first, second, taxonomy)
    processed = resolve_within_project(
        project_root, first.research.data.paths.processed
    )
    output = processed / "reports" / "phase7" / run_id
    output.mkdir(parents=True, exist_ok=True)
    public_episodes = episodes.drop(columns=["_start_index", "_end_index"])
    timeline.to_parquet(output / "state_timeline.parquet", index=False)
    public_episodes.to_parquet(output / "state_episodes.parquet", index=False)
    transitions.to_parquet(output / "state_transitions.parquet", index=False)
    episode_stats.to_csv(output / "episode_statistics.csv", index=False)
    occupancy.to_csv(output / "state_occupancy.csv", index=False)
    matrix.to_csv(output / "transition_matrix.csv", index=False)
    hazard.to_csv(output / "balance_hazard.csv", index=False)
    clocks.to_csv(output / "transitions_by_clock.csv", index=False)
    signatures.to_csv(output / "transition_signatures.csv", index=False)
    antecedents.to_csv(output / "transition_antecedents.csv", index=False)
    events.to_parquet(output / "opening_control_events.parquet", index=False)
    opening_stats.to_csv(output / "opening_control_statistics.csv", index=False)
    opening_differences.to_csv(
        output / "opening_control_differences.csv", index=False
    )
    data_quality = {
        "valid": bool(invariants["passed"]),
        "execution_invariants": invariants,
        "interpretation": "exploratory_taxonomy_no_trading_gate",
    }
    _write_json(data_quality, output / "data_quality.json")
    figures = _create_figures(episodes, clocks, opening_stats, output / "figures")
    report = _render_report(
        occupancy,
        episodes,
        episode_stats,
        matrix,
        hazard,
        signatures,
        antecedents,
        opening_stats,
        opening_differences,
        invariants,
    )
    (output / "report.md").write_text(report, encoding="utf-8")

    combined_config = {
        "first": first.model_dump(mode="json"),
        "second": second.model_dump(mode="json"),
        "taxonomy": taxonomy.model_dump(mode="json"),
    }
    manifest = {
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "research_status": "exploratory_taxonomy_no_trading_decision",
        "config": combined_config,
        "config_sha256": _hash_json(combined_config),
        "git": _git_state(project_root),
        "data_quality_valid": data_quality["valid"],
        "runtime": {
            "python": platform.python_version(),
            "packages": {
                package: importlib.metadata.version(package)
                for package in ("numpy", "pandas", "pyarrow", "matplotlib", "scipy")
            },
        },
        "rows": {
            "timeline": len(timeline),
            "episodes": len(episodes),
            "transitions": len(transitions),
            "opening_control_events": len(events),
            "episode_statistics": len(episode_stats),
            "state_occupancy": len(occupancy),
            "opening_control_statistics": len(opening_stats),
            "transition_antecedents": len(antecedents),
            "opening_control_differences": len(opening_differences),
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
