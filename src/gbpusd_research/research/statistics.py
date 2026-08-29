"""Descriptive and paired-bootstrap statistics for Phase 1."""

from __future__ import annotations

import numpy as np
import pandas as pd


def descriptive_summary(
    events: pd.DataFrame, horizons: tuple[int, ...]
) -> pd.DataFrame:
    rows = []
    eligible = events[events["eligible"]].copy()
    for (kind, session), group in eligible.groupby(
        ["event_kind", "session_name"], observed=True
    ):
        for horizon in horizons:
            for metric in ("range_pips", "abs_return_pips", "return_pips"):
                values = group[f"fwd_{horizon}_{metric}"].dropna()
                if values.empty:
                    continue
                rows.append(
                    {
                        "event_kind": kind,
                        "session_name": session,
                        "horizon_minutes": horizon,
                        "metric": metric,
                        "count": len(values),
                        "mean": values.mean(),
                        "std": values.std(ddof=1),
                        "median": values.median(),
                        "mad": (values - values.median()).abs().median(),
                        **{
                            f"p{int(q * 100):02d}": values.quantile(q)
                            for q in (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
                        },
                    }
                )
    return pd.DataFrame(rows)


def summary_by_year(events: pd.DataFrame, horizons: tuple[int, ...]) -> pd.DataFrame:
    rows = []
    eligible = events[events["eligible"]]
    for (kind, session, year), group in eligible.groupby(
        ["event_kind", "session_name", "calendar_year"], observed=True
    ):
        for horizon in horizons:
            values = group[f"fwd_{horizon}_range_pips"].dropna()
            if values.empty:
                continue
            rows.append(
                {
                    "event_kind": kind,
                    "session_name": session,
                    "calendar_year": year,
                    "horizon_minutes": horizon,
                    "count": len(values),
                    "mean_range_pips": values.mean(),
                    "median_range_pips": values.median(),
                    "p25_range_pips": values.quantile(0.25),
                    "p75_range_pips": values.quantile(0.75),
                }
            )
    return pd.DataFrame(rows)


def paired_bootstrap_comparisons(
    openings: pd.DataFrame,
    controls: pd.DataFrame,
    *,
    horizons: tuple[int, ...],
    resamples: int,
    confidence_level: float,
    random_seed: int,
) -> pd.DataFrame:
    """Compare paired events; one row is one session/control/horizon/metric."""

    alpha = (1 - confidence_level) / 2
    rows = []
    opening_columns = ["event_id", "session_name", "calendar_year", "eligible"] + [
        f"fwd_{horizon}_{metric}"
        for horizon in horizons
        for metric in ("range_pips", "abs_return_pips", "range_over_pre60")
    ]
    for control_kind, control_group in controls.groupby("event_kind", observed=True):
        paired = openings[opening_columns].merge(
            control_group,
            left_on="event_id",
            right_on="matched_event_id",
            suffixes=("_open", "_control"),
        )
        paired = paired[paired["eligible_open"] & paired["eligible_control"]]
        scopes = [("all", None, paired)]
        scopes.extend(
            ("calendar_year", int(year), group)
            for year, group in paired.groupby("calendar_year_open", observed=True)
        )
        for analysis_scope, calendar_year, scoped in scopes:
            for session, session_group in scoped.groupby(
                "session_name_open", observed=True
            ):
                for horizon in horizons:
                    for metric in (
                        "range_pips",
                        "abs_return_pips",
                        "range_over_pre60",
                    ):
                        rows.extend(
                            _bootstrap_row(
                                session_group,
                                control_kind=str(control_kind),
                                session=str(session),
                                horizon=horizon,
                                metric=metric,
                                analysis_scope=analysis_scope,
                                calendar_year=calendar_year,
                                resamples=resamples,
                                alpha=alpha,
                                random_seed=random_seed,
                            )
                        )
    return pd.DataFrame(rows)


def _bootstrap_row(
    session_group: pd.DataFrame,
    *,
    control_kind: str,
    session: str,
    horizon: int,
    metric: str,
    analysis_scope: str,
    calendar_year: int | None,
    resamples: int,
    alpha: float,
    random_seed: int,
) -> list[dict[str, object]]:
    column = f"fwd_{horizon}_{metric}"
    clean = session_group[[f"{column}_open", f"{column}_control"]].dropna()
    if clean.empty:
        return []
    differences = (clean[f"{column}_open"] - clean[f"{column}_control"]).to_numpy()
    digest_seed = (
        random_seed
        + horizon * 1009
        + (calendar_year or 0)
        + sum(ord(char) for char in f"{control_kind}{session}{metric}")
    )
    rng = np.random.default_rng(digest_seed)
    indices = rng.integers(0, len(differences), size=(resamples, len(differences)))
    samples = differences[indices]
    mean_samples = samples.mean(axis=1)
    median_samples = np.median(samples, axis=1)
    return [
        {
            "analysis_scope": analysis_scope,
            "calendar_year": calendar_year,
            "control_kind": control_kind,
            "session_name": session,
            "horizon_minutes": horizon,
            "metric": metric,
            "pair_count": len(differences),
            "opening_mean": clean[f"{column}_open"].mean(),
            "control_mean": clean[f"{column}_control"].mean(),
            "mean_difference": differences.mean(),
            "mean_ci_low": np.quantile(mean_samples, alpha),
            "mean_ci_high": np.quantile(mean_samples, 1 - alpha),
            "median_difference": np.median(differences),
            "median_ci_low": np.quantile(median_samples, alpha),
            "median_ci_high": np.quantile(median_samples, 1 - alpha),
            "probability_opening_exceeds_control": np.mean(differences > 0),
        }
    ]
