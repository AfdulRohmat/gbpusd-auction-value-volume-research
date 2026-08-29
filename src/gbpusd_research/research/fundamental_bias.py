"""Conditional outcomes and statistics for the Phase-3 policy bias."""

from __future__ import annotations

import numpy as np
import pandas as pd


def attach_fundamental_outcomes(
    events: pd.DataFrame,
    horizons: tuple[int, ...],
    *,
    relation_column: str = "policy_value_relation",
    unavailable_reason: str = "policy_history_unavailable",
) -> pd.DataFrame:
    """Add directional outcomes while preserving event-time policy features."""

    output = events.copy()
    bias = output["fundamental_bias"].fillna(0)
    reversion_direction = np.select(
        [
            output["value_state"].eq("above_value"),
            output["value_state"].eq("below_value"),
        ],
        [-1, 1],
        default=0,
    )
    output["value_reversion_direction"] = reversion_direction
    output[relation_column] = np.select(
        [
            ~output["fundamental_available"],
            reversion_direction == 0,
            bias.eq(0),
            bias.eq(reversion_direction),
            bias.eq(-reversion_direction),
        ],
        [
            "unavailable",
            "inside_value",
            "neutral",
            "supports_reversion",
            "opposes_reversion",
        ],
        default="unclassified",
    )
    for horizon in horizons:
        prefix = f"fundamental_fwd_{horizon}"
        output[f"{prefix}_bias_aligned_return_pips"] = (
            bias * output[f"fwd_{horizon}_return_pips"]
        ).where(bias.ne(0))
        output[f"{prefix}_bias_aligned_mfe_pips"] = np.select(
            [bias.gt(0), bias.lt(0)],
            [
                output[f"fwd_{horizon}_up_excursion_pips"],
                output[f"fwd_{horizon}_down_excursion_pips"],
            ],
            default=np.nan,
        )
        output[f"{prefix}_bias_aligned_mae_pips"] = np.select(
            [bias.gt(0), bias.lt(0)],
            [
                output[f"fwd_{horizon}_down_excursion_pips"],
                output[f"fwd_{horizon}_up_excursion_pips"],
            ],
            default=np.nan,
        )
        output[f"{prefix}_reversion_aligned_return_pips"] = (
            reversion_direction * output[f"fwd_{horizon}_return_pips"]
        ).where(reversion_direction != 0)
    output["fundamental_eligible"] = (
        output["value_eligible"] & output["fundamental_available"]
    )
    output["fundamental_exclusion_reason"] = np.select(
        [~output["value_eligible"], ~output["fundamental_available"]],
        ["phase2_event_ineligible", unavailable_reason],
        default=None,
    )
    return output


def fundamental_conditional_statistics(
    events: pd.DataFrame,
    horizons: tuple[int, ...],
    *,
    minimum_group_size: int,
) -> pd.DataFrame:
    """Summarize policy bias and sufficiently populated value/bias cells."""

    eligible = events[events["fundamental_eligible"]]
    rows = []
    groupings = {
        "bias": ["session_name", "fundamental_bias_label"],
        "value_bias": [
            "session_name",
            "value_state",
            "fundamental_bias_label",
        ],
    }
    for grouping, columns in groupings.items():
        for keys, group in eligible.groupby(columns, observed=True):
            keys = keys if isinstance(keys, tuple) else (keys,)
            if grouping == "value_bias" and len(group) < minimum_group_size:
                continue
            identity = dict(zip(columns, keys, strict=True))
            for horizon in horizons:
                metrics = {
                    "return_pips": f"fwd_{horizon}_return_pips",
                    "range_pips": f"fwd_{horizon}_range_pips",
                    "bias_aligned_return_pips": (
                        f"fundamental_fwd_{horizon}_bias_aligned_return_pips"
                    ),
                    "bias_aligned_mfe_pips": (
                        f"fundamental_fwd_{horizon}_bias_aligned_mfe_pips"
                    ),
                    "bias_aligned_mae_pips": (
                        f"fundamental_fwd_{horizon}_bias_aligned_mae_pips"
                    ),
                }
                for metric, column in metrics.items():
                    values = group[column].dropna()
                    if values.empty:
                        continue
                    rows.append(
                        {
                            "grouping": grouping,
                            **identity,
                            "horizon_minutes": horizon,
                            "metric": metric,
                            "count": len(values),
                            "mean": values.mean(),
                            "median": values.median(),
                            "p25": values.quantile(0.25),
                            "p75": values.quantile(0.75),
                        }
                    )
    return pd.DataFrame(rows)


def _one_sample_bootstrap(
    values: np.ndarray,
    *,
    resamples: int,
    alpha: float,
    rng: np.random.Generator,
) -> tuple[float, float, float]:
    indices = rng.integers(0, len(values), size=(resamples, len(values)))
    samples = values[indices].mean(axis=1)
    return (
        float(values.mean()),
        float(np.quantile(samples, alpha)),
        float(np.quantile(samples, 1 - alpha)),
    )


def _two_sample_bootstrap(
    first: np.ndarray,
    second: np.ndarray,
    *,
    resamples: int,
    alpha: float,
    rng: np.random.Generator,
) -> tuple[float, float, float]:
    first_samples = first[
        rng.integers(0, len(first), size=(resamples, len(first)))
    ].mean(axis=1)
    second_samples = second[
        rng.integers(0, len(second), size=(resamples, len(second)))
    ].mean(axis=1)
    differences = first_samples - second_samples
    return (
        float(first.mean() - second.mean()),
        float(np.quantile(differences, alpha)),
        float(np.quantile(differences, 1 - alpha)),
    )


def fundamental_comparisons(
    events: pd.DataFrame,
    *,
    horizons: tuple[int, ...],
    resamples: int,
    confidence_level: float,
    random_seed: int,
    relation_column: str = "policy_value_relation",
) -> pd.DataFrame:
    """Run frozen directional and value-reversion bootstrap contrasts."""

    eligible = events[events["fundamental_eligible"]]
    alpha = (1 - confidence_level) / 2
    rows = []
    for session, group in eligible.groupby("session_name", observed=True):
        session_seed = sum(map(ord, str(session)))
        for horizon in horizons:
            aligned = (
                group[f"fundamental_fwd_{horizon}_bias_aligned_return_pips"]
                .dropna()
                .to_numpy()
            )
            if len(aligned):
                mean, low, high = _one_sample_bootstrap(
                    aligned,
                    resamples=resamples,
                    alpha=alpha,
                    rng=np.random.default_rng(
                        random_seed + session_seed + horizon * 101
                    ),
                )
                rows.append(
                    {
                        "contrast": "bias_aligned_return_vs_zero",
                        "session_name": session,
                        "horizon_minutes": horizon,
                        "first_count": len(aligned),
                        "second_count": 0,
                        "first_mean": aligned.mean(),
                        "second_mean": 0.0,
                        "mean_difference": mean,
                        "ci_low": low,
                        "ci_high": high,
                    }
                )
            outcome = f"fundamental_fwd_{horizon}_reversion_aligned_return_pips"
            supports = (
                group[group[relation_column].eq("supports_reversion")][outcome]
                .dropna()
                .to_numpy()
            )
            opposes = (
                group[group[relation_column].eq("opposes_reversion")][outcome]
                .dropna()
                .to_numpy()
            )
            if len(supports) and len(opposes):
                difference, low, high = _two_sample_bootstrap(
                    supports,
                    opposes,
                    resamples=resamples,
                    alpha=alpha,
                    rng=np.random.default_rng(
                        random_seed + session_seed + horizon * 1009
                    ),
                )
                rows.append(
                    {
                        "contrast": "supports_minus_opposes_value_reversion",
                        "session_name": session,
                        "horizon_minutes": horizon,
                        "first_count": len(supports),
                        "second_count": len(opposes),
                        "first_mean": supports.mean(),
                        "second_mean": opposes.mean(),
                        "mean_difference": difference,
                        "ci_low": low,
                        "ci_high": high,
                    }
                )
    return pd.DataFrame(rows)
