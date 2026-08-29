"""Phase-1 report tables, figures, and Markdown narrative."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd


def create_figures(
    events: pd.DataFrame, bars: pd.DataFrame, output_dir: Path
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = output_dir.parent / ".plot_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache_dir))
    os.environ.setdefault("XDG_CACHE_HOME", str(cache_dir))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    eligible = events[events["eligible"]]
    paths = []

    groups = []
    labels = []
    for (session, kind), group in eligible.groupby(
        ["session_name", "event_kind"], observed=True
    ):
        groups.append(group["fwd_60_range_pips"].dropna())
        labels.append(f"{session}\n{kind}")
    figure, axis = plt.subplots(figsize=(10, 5))
    axis.boxplot(groups, tick_labels=labels, showfliers=False)
    axis.set_ylabel("60-minute range (pips)")
    axis.set_title("Opening and control distributions")
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    path = output_dir / "distribution_by_session.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    paths.append(path)

    openings = eligible[eligible["event_kind"].eq("session_open")]
    spread_rows = []
    indexed_spreads = bars.set_index("timestamp")["spread_median_pips"]
    for event in openings.itertuples(index=False):
        opened = pd.Timestamp(event.event_timestamp_utc)
        for offset in range(-30, 91, 5):
            timestamp = opened + pd.Timedelta(offset, unit="min")
            if timestamp in indexed_spreads.index:
                spread_rows.append(
                    {
                        "session_name": event.session_name,
                        "offset_minutes": offset,
                        "spread_pips": indexed_spreads.loc[timestamp],
                    }
                )
    spread_profile = pd.DataFrame(spread_rows)
    figure, axis = plt.subplots(figsize=(8, 5))
    if not spread_profile.empty:
        profile = (
            spread_profile.groupby(["offset_minutes", "session_name"], observed=True)[
                "spread_pips"
            ]
            .median()
            .unstack()
        )
        profile.plot(ax=axis)
    axis.axvline(0, color="black", linewidth=1, linestyle="--")
    axis.set_xlabel("Minutes relative to session open")
    axis.set_ylabel("Median quoted spread (pips)")
    axis.set_title("Spread around session open")
    axis.grid(alpha=0.25)
    figure.tight_layout()
    path = output_dir / "spread_around_open.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    paths.append(path)

    opening = openings
    figure, axis = plt.subplots(figsize=(8, 5))
    horizons = [5, 15, 30, 60, 90]
    for session, group in opening.groupby("session_name", observed=True):
        medians = [group[f"fwd_{horizon}_range_pips"].median() for horizon in horizons]
        axis.plot(horizons, medians, marker="o", label=session)
    axis.set_xlabel("Horizon (minutes)")
    axis.set_ylabel("Median range (pips)")
    axis.set_title("Opening range by horizon")
    axis.legend()
    axis.grid(alpha=0.25)
    figure.tight_layout()
    path = output_dir / "quantiles_by_horizon.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    paths.append(path)

    figure, axis = plt.subplots(figsize=(8, 5))
    yearly = (
        opening.groupby(["calendar_year", "session_name"], observed=True)[
            "fwd_60_range_over_pre60"
        ]
        .median()
        .unstack()
    )
    yearly.plot(kind="bar", ax=axis)
    axis.set_ylabel("Median 60m range / pre-open 60m range")
    axis.set_title("Normalized opening range by year")
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    path = output_dir / "normalized_range_by_year.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    paths.append(path)
    return paths


def render_markdown(
    openings: pd.DataFrame,
    controls: pd.DataFrame,
    comparisons: pd.DataFrame,
    *,
    data_quality: dict[str, Any],
    start: str,
    end: str,
) -> str:
    eligible_openings = openings[openings["eligible"]]
    gate = data_quality["research_gate"]
    gate_label = "PASS" if gate["passed"] else "FAIL"
    lines = [
        "# GBPUSD Phase-1 Session Event Study",
        "",
        f"Study interval: `{start}` inclusive to `{end}` exclusive.",
        "",
        "This report measures movement, not strategy profitability. No entry or P&L "
        "rules are evaluated.",
        "",
        "## Data coverage",
        "",
        f"- Opening events: {len(openings)} total; {len(eligible_openings)} eligible.",
        f"- Control events: {len(controls)} total; "
        f"{int(controls['eligible'].sum())} eligible.",
        f"- Registered Phase-1 gate: **{gate_label}**.",
        "",
        "### Opening coverage by session and year",
        "",
        "| Year | Session | Scheduled | Eligible | Coverage |",
        "|---:|---:|---:|---:|---:|",
    ]
    for row in data_quality["opening_coverage_by_session_year"]:
        lines.append(
            f"| {row['calendar_year']} | {row['session_name']} | "
            f"{row['scheduled']} | {row['eligible']} | "
            f"{row['eligible_ratio']:.1%} |"
        )
    lines.extend(
        [
            "",
            "## Primary endpoint: 60-minute range",
            "",
            "| Control | Session | Pairs | Opening mean | Control mean | Difference "
            "| 95% CI | P(open > control) |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    primary = comparisons[
        comparisons["analysis_scope"].eq("all")
        & comparisons["horizon_minutes"].eq(60)
        & comparisons["metric"].eq("range_pips")
    ]
    for row in primary.itertuples(index=False):
        lines.append(
            f"| {row.control_kind} | {row.session_name} | {row.pair_count} | "
            f"{row.opening_mean:.2f} | {row.control_mean:.2f} | "
            f"{row.mean_difference:.2f} | [{row.mean_ci_low:.2f}, "
            f"{row.mean_ci_high:.2f}] | "
            f"{row.probability_opening_exceeds_control:.1%} |"
        )
    lines.extend(
        [
            "",
            "### Year-by-year primary endpoint",
            "",
            "| Year | Control | Session | Pairs | Difference | 95% CI |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
    )
    yearly = comparisons[
        comparisons["analysis_scope"].eq("calendar_year")
        & comparisons["horizon_minutes"].eq(60)
        & comparisons["metric"].eq("range_pips")
    ]
    for row in yearly.itertuples(index=False):
        lines.append(
            f"| {int(row.calendar_year)} | {row.control_kind} | "
            f"{row.session_name} | {row.pair_count} | {row.mean_difference:.2f} | "
            f"[{row.mean_ci_low:.2f}, {row.mean_ci_high:.2f}] |"
        )
    failed_checks = [name for name, passed in gate["checks"].items() if not passed]
    lines.extend(["", "## Registered research gate", ""])
    if failed_checks:
        lines.append(
            "The run failed: " + ", ".join(f"`{name}`" for name in failed_checks) + "."
        )
        lines.append("")
        lines.append(
            "Primary-effect estimates must not be treated as validation evidence "
            "until the coverage problem is resolved with a consistent data source."
        )
    else:
        lines.append("All pre-registered Phase-1 checks passed.")
    interpretation = (
        "The registered checks passed, which supports studying value state next."
        if gate["passed"]
        else "Phase 2 should not start from this run; repair or replace the incomplete "
        "source period and rerun the frozen specification."
    )
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            interpretation,
            "This event study does not establish direction, execution quality, or "
            "profitability after costs.",
            "",
        ]
    )
    return "\n".join(lines)
