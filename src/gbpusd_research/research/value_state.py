"""Value-state event outcomes and conditional statistics."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd


def _has_consecutive(values: pd.Series, count: int) -> bool:
    if len(values) < count:
        return False
    return bool(values.astype("int8").rolling(count).sum().eq(count).any())


def attach_value_outcomes(
    events: pd.DataFrame,
    bars: pd.DataFrame,
    *,
    pip_size: float,
    boundary_buffer_pips: float,
    acceptance_consecutive_closes: int,
    horizons: tuple[int, ...],
) -> pd.DataFrame:
    """Attach future transition labels without changing event-time state."""

    output = events.copy()
    ordered = bars.copy()
    ordered["timestamp"] = pd.to_datetime(ordered["timestamp"], utc=True)
    ordered = ordered.sort_values("timestamp", kind="stable").reset_index(drop=True)
    timestamp = ordered["timestamp"]
    rows = []
    buffer_price = boundary_buffer_pips * pip_size
    for event in output.itertuples(index=False):
        row: dict[str, object] = {}
        opened = pd.Timestamp(event.event_timestamp_utc)
        state = getattr(event, "value_state", None)
        profile_available = bool(getattr(event, "profile_available", False))
        for horizon in horizons:
            start = timestamp.searchsorted(opened, side="left")
            end = timestamp.searchsorted(
                opened + timedelta(minutes=horizon), side="left"
            )
            frame = ordered.iloc[start:end]
            prefix = f"value_fwd_{horizon}"
            if not profile_available or frame.empty:
                row[f"{prefix}_reentered"] = np.nan
                row[f"{prefix}_acceptance_above"] = np.nan
                row[f"{prefix}_acceptance_below"] = np.nan
                row[f"{prefix}_state_aligned_return_pips"] = np.nan
                row[f"{prefix}_state_aligned_mfe_pips"] = np.nan
                row[f"{prefix}_state_aligned_mae_pips"] = np.nan
                continue
            closes = frame["mid_close"]
            above = closes > event.previous_vah + buffer_price
            below = closes < event.previous_val - buffer_price
            row[f"{prefix}_acceptance_above"] = _has_consecutive(
                above, acceptance_consecutive_closes
            )
            row[f"{prefix}_acceptance_below"] = _has_consecutive(
                below, acceptance_consecutive_closes
            )
            if state == "above_value":
                direction = 1
                reentered = bool((closes <= event.previous_vah).any())
                mfe = getattr(event, f"fwd_{horizon}_up_excursion_pips")
                mae = getattr(event, f"fwd_{horizon}_down_excursion_pips")
            elif state == "below_value":
                direction = -1
                reentered = bool((closes >= event.previous_val).any())
                mfe = getattr(event, f"fwd_{horizon}_down_excursion_pips")
                mae = getattr(event, f"fwd_{horizon}_up_excursion_pips")
            else:
                direction = 0
                reentered = np.nan
                mfe = np.nan
                mae = np.nan
            row[f"{prefix}_reentered"] = reentered
            row[f"{prefix}_state_aligned_return_pips"] = (
                direction * getattr(event, f"fwd_{horizon}_return_pips")
                if direction
                else np.nan
            )
            row[f"{prefix}_state_aligned_mfe_pips"] = mfe
            row[f"{prefix}_state_aligned_mae_pips"] = mae
        rows.append(row)
    output = pd.concat([output.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    output["value_eligible"] = (
        output["eligible"]
        & output["profile_available"]
        & output["vwap_available"]
        & output["vwap_slope_pips"].notna()
    )
    output["value_exclusion_reason"] = np.select(
        [
            ~output["eligible"],
            ~output["profile_available"],
            ~output["vwap_available"],
            output["vwap_slope_pips"].isna(),
        ],
        [
            "phase1_event_ineligible",
            "previous_profile_unavailable",
            "vwap_unavailable",
            "vwap_slope_unavailable",
        ],
        default=None,
    )
    return output


def conditional_statistics(
    events: pd.DataFrame, horizons: tuple[int, ...], *, minimum_group_size: int
) -> pd.DataFrame:
    """Summarize value, VWAP, and sufficiently populated joint states."""

    eligible = events[events["value_eligible"]].copy()
    rows = []
    groupings = {
        "value_state": ["session_name", "value_state"],
        "vwap_state": ["session_name", "vwap_state"],
        "joint_state": ["session_name", "value_state", "vwap_state"],
    }
    for grouping, columns in groupings.items():
        for keys, group in eligible.groupby(columns, observed=True):
            keys = keys if isinstance(keys, tuple) else (keys,)
            if grouping == "joint_state" and len(group) < minimum_group_size:
                continue
            identity = dict(zip(columns, keys, strict=True))
            for horizon in horizons:
                for metric in ("range_pips", "return_pips", "abs_return_pips"):
                    values = group[f"fwd_{horizon}_{metric}"].dropna()
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


def continuous_feature_associations(
    events: pd.DataFrame, horizons: tuple[int, ...]
) -> pd.DataFrame:
    """Report exploratory rank associations without introducing thresholds."""

    eligible = events[events["value_eligible"]]
    rows = []
    features = ("vwap_distance_pips", "vwap_zscore", "vwap_slope_pips")
    metrics = ("return_pips", "range_pips")
    for session, group in eligible.groupby("session_name", observed=True):
        for horizon in horizons:
            for feature in features:
                for metric in metrics:
                    outcome = f"fwd_{horizon}_{metric}"
                    pair = group[[feature, outcome]].dropna()
                    rows.append(
                        {
                            "session_name": session,
                            "horizon_minutes": horizon,
                            "feature": feature,
                            "outcome": outcome,
                            "count": len(pair),
                            "spearman_rho": pair[feature].corr(
                                pair[outcome], method="spearman"
                            ),
                        }
                    )
    return pd.DataFrame(rows)


def _bootstrap_difference(
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


def value_state_comparisons(
    events: pd.DataFrame,
    *,
    horizons: tuple[int, ...],
    resamples: int,
    confidence_level: float,
    random_seed: int,
) -> pd.DataFrame:
    """Registered outside-vs-inside and outside-continuation contrasts."""

    eligible = events[events["value_eligible"]].copy()
    eligible["outside_value"] = eligible["value_state"].isin(
        ["above_value", "below_value"]
    )
    alpha = (1 - confidence_level) / 2
    rows = []
    for session, group in eligible.groupby("session_name", observed=True):
        for horizon in horizons:
            outside_range = (
                group[group["outside_value"]][f"fwd_{horizon}_range_pips"]
                .dropna()
                .to_numpy()
            )
            inside_range = (
                group[~group["outside_value"]][f"fwd_{horizon}_range_pips"]
                .dropna()
                .to_numpy()
            )
            if len(outside_range) and len(inside_range):
                rng = np.random.default_rng(
                    random_seed + horizon * 101 + sum(map(ord, str(session)))
                )
                difference, low, high = _bootstrap_difference(
                    outside_range,
                    inside_range,
                    resamples=resamples,
                    alpha=alpha,
                    rng=rng,
                )
                rows.append(
                    {
                        "contrast": "outside_minus_inside_range",
                        "session_name": session,
                        "horizon_minutes": horizon,
                        "first_count": len(outside_range),
                        "second_count": len(inside_range),
                        "first_mean": outside_range.mean(),
                        "second_mean": inside_range.mean(),
                        "mean_difference": difference,
                        "ci_low": low,
                        "ci_high": high,
                    }
                )
            continuation = (
                group[group["outside_value"]][
                    f"value_fwd_{horizon}_state_aligned_return_pips"
                ]
                .dropna()
                .to_numpy()
            )
            if len(continuation):
                rng = np.random.default_rng(
                    random_seed + horizon * 1009 + sum(map(ord, str(session)))
                )
                indices = rng.integers(
                    0, len(continuation), size=(resamples, len(continuation))
                )
                samples = continuation[indices].mean(axis=1)
                rows.append(
                    {
                        "contrast": "outside_state_aligned_return_vs_zero",
                        "session_name": session,
                        "horizon_minutes": horizon,
                        "first_count": len(continuation),
                        "second_count": 0,
                        "first_mean": continuation.mean(),
                        "second_mean": 0.0,
                        "mean_difference": continuation.mean(),
                        "ci_low": np.quantile(samples, alpha),
                        "ci_high": np.quantile(samples, 1 - alpha),
                    }
                )
    return pd.DataFrame(rows)
