"""Point-in-time GBP-minus-USD relative fundamental-strength features."""

from __future__ import annotations

import numpy as np
import pandas as pd

from gbpusd_research.config import FundamentalStrengthConfig

CURRENCY_TIMEZONES = {
    "GBP": "Europe/London",
    "USD": "America/New_York",
}
EARNINGS_INDICATORS = {
    "GBP": "regular_earnings_yoy",
    "USD": "average_hourly_earnings_yoy",
}


def _signal(value: float) -> int:
    return int(np.sign(value))


def _bias(score: int, threshold: int) -> int:
    if score >= threshold:
        return 1
    if score <= -threshold:
        return -1
    return 0


def _label(value: int) -> str:
    return "long" if value > 0 else "short" if value < 0 else "neutral"


def _latest_two_at(
    frame: pd.DataFrame,
    timestamp: pd.Timestamp,
    *,
    currency: str,
    indicator: str | None = None,
) -> tuple[pd.Series, pd.Series] | None:
    available = frame[
        frame["currency"].eq(currency) & frame["available_at_utc"].le(timestamp)
    ]
    if indicator is not None:
        available = available[available["indicator"].eq(indicator)]
    if len(available) < 2:
        return None
    return available.iloc[-1], available.iloc[-2]


def _yield_pair(
    yields: pd.DataFrame,
    timestamp: pd.Timestamp,
    *,
    currency: str,
    lookback_observations: int,
) -> tuple[pd.Series, pd.Series, pd.Timestamp] | None:
    local_date = (
        timestamp.tz_convert(CURRENCY_TIMEZONES[currency])
        .tz_localize(None)
        .normalize()
    )
    available = yields[
        yields["currency"].eq(currency)
        & yields["available_at_utc"].le(timestamp)
        & yields["observation_date"].lt(local_date)
    ]
    if len(available) <= lookback_observations:
        return None
    return available.iloc[-1], available.iloc[-(lookback_observations + 1)], local_date


def _currency_features(
    timestamp: pd.Timestamp,
    currency: str,
    policy_events: pd.DataFrame,
    macro_events: pd.DataFrame,
    yields: pd.DataFrame,
    config: FundamentalStrengthConfig,
) -> dict[str, object] | None:
    prefix = currency.lower()
    policy = _latest_two_at(policy_events, timestamp, currency=currency)
    headline = _latest_two_at(
        macro_events,
        timestamp,
        currency=currency,
        indicator="headline_cpi_yoy",
    )
    core = _latest_two_at(
        macro_events,
        timestamp,
        currency=currency,
        indicator="core_cpi_yoy",
    )
    labor = _latest_two_at(
        macro_events,
        timestamp,
        currency=currency,
        indicator=EARNINGS_INDICATORS[currency],
    )
    yield_pair = _yield_pair(
        yields,
        timestamp,
        currency=currency,
        lookback_observations=config.scoring.yield_lookback_observations,
    )
    if any(value is None for value in (policy, headline, core, labor, yield_pair)):
        return None
    assert policy is not None
    assert headline is not None
    assert core is not None
    assert labor is not None
    assert yield_pair is not None
    current_policy, previous_policy = policy
    current_headline, previous_headline = headline
    current_core, previous_core = core
    current_labor, previous_labor = labor
    current_yield, previous_yield, yield_cutoff_date = yield_pair

    policy_change = float(current_policy["rate_mid_pct"]) - float(
        previous_policy["rate_mid_pct"]
    )
    policy_score = _signal(policy_change)
    headline_change = float(current_headline["value"]) - float(
        previous_headline["value"]
    )
    core_change = float(current_core["value"]) - float(previous_core["value"])
    headline_signal = _signal(headline_change)
    core_signal = _signal(core_change)
    inflation_score = _signal(headline_signal + core_signal)
    labor_change = float(current_labor["value"]) - float(previous_labor["value"])
    labor_score = _signal(labor_change)
    yield_change = float(current_yield["yield_2y_pct"]) - float(
        previous_yield["yield_2y_pct"]
    )
    deadband = config.scoring.yield_deadband_pct
    yield_score = (
        1 if yield_change >= deadband else -1 if yield_change <= -deadband else 0
    )
    currency_score = policy_score + inflation_score + labor_score + yield_score
    weights = config.scoring.robustness_weights
    weighted_score = (
        weights.policy * policy_score
        + weights.inflation * inflation_score
        + weights.labor * labor_score
        + weights.yield_expectation * yield_score
    )

    return {
        f"{prefix}_policy_current_event_id": current_policy["event_id"],
        f"{prefix}_policy_previous_event_id": previous_policy["event_id"],
        f"{prefix}_policy_available_at": current_policy["available_at_utc"],
        f"{prefix}_policy_previous_available_at": previous_policy[
            "available_at_utc"
        ],
        f"{prefix}_policy_rate_pct": float(current_policy["rate_mid_pct"]),
        f"{prefix}_policy_previous_rate_pct": float(
            previous_policy["rate_mid_pct"]
        ),
        f"{prefix}_policy_change_pct": policy_change,
        f"{prefix}_policy_score": policy_score,
        f"{prefix}_headline_current_event_id": current_headline["event_id"],
        f"{prefix}_headline_previous_event_id": previous_headline["event_id"],
        f"{prefix}_headline_available_at": current_headline["available_at_utc"],
        f"{prefix}_headline_previous_available_at": previous_headline[
            "available_at_utc"
        ],
        f"{prefix}_headline_cpi_yoy": float(current_headline["value"]),
        f"{prefix}_headline_previous_cpi_yoy": float(previous_headline["value"]),
        f"{prefix}_headline_change_pct": headline_change,
        f"{prefix}_headline_signal": headline_signal,
        f"{prefix}_core_current_event_id": current_core["event_id"],
        f"{prefix}_core_previous_event_id": previous_core["event_id"],
        f"{prefix}_core_available_at": current_core["available_at_utc"],
        f"{prefix}_core_previous_available_at": previous_core["available_at_utc"],
        f"{prefix}_core_cpi_yoy": float(current_core["value"]),
        f"{prefix}_core_previous_cpi_yoy": float(previous_core["value"]),
        f"{prefix}_core_change_pct": core_change,
        f"{prefix}_core_signal": core_signal,
        f"{prefix}_inflation_score": inflation_score,
        f"{prefix}_labor_current_event_id": current_labor["event_id"],
        f"{prefix}_labor_previous_event_id": previous_labor["event_id"],
        f"{prefix}_labor_available_at": current_labor["available_at_utc"],
        f"{prefix}_labor_previous_available_at": previous_labor["available_at_utc"],
        f"{prefix}_earnings_yoy": float(current_labor["value"]),
        f"{prefix}_earnings_previous_yoy": float(previous_labor["value"]),
        f"{prefix}_earnings_change_pct": labor_change,
        f"{prefix}_labor_score": labor_score,
        f"{prefix}_yield_cutoff_date": yield_cutoff_date,
        f"{prefix}_yield_observation_date": current_yield["observation_date"],
        f"{prefix}_yield_lookback_date": previous_yield["observation_date"],
        f"{prefix}_yield_available_at": current_yield["available_at_utc"],
        f"{prefix}_yield_lookback_available_at": previous_yield[
            "available_at_utc"
        ],
        f"{prefix}_yield_2y_pct": float(current_yield["yield_2y_pct"]),
        f"{prefix}_yield_lookback_2y_pct": float(
            previous_yield["yield_2y_pct"]
        ),
        f"{prefix}_yield_change_pct": yield_change,
        f"{prefix}_yield_expectation_score": yield_score,
        f"{prefix}_score": currency_score,
        f"{prefix}_weighted_score": weighted_score,
    }


def attach_relative_fundamental_strength(
    events: pd.DataFrame,
    policy_events: pd.DataFrame,
    macro_events: pd.DataFrame,
    yields: pd.DataFrame,
    config: FundamentalStrengthConfig,
) -> pd.DataFrame:
    """Attach frozen equal-weight and weighted sensitivity biases per session."""

    rows = []
    for event in events.itertuples(index=False):
        opened = pd.Timestamp(event.event_timestamp_utc)
        currencies = {
            currency: _currency_features(
                opened,
                currency,
                policy_events,
                macro_events,
                yields,
                config,
            )
            for currency in ("GBP", "USD")
        }
        missing = [
            currency for currency, values in currencies.items() if values is None
        ]
        if missing:
            rows.append(
                {
                    "fundamental_available": False,
                    "fundamental_model": "relative_strength_v1",
                    "fundamental_unavailable_reason": (
                        "missing_complete_history:" + ",".join(missing)
                    ),
                    "fundamental_bias": np.nan,
                    "fundamental_bias_label": "unavailable",
                    "weighted_fundamental_bias": np.nan,
                    "weighted_fundamental_bias_label": "unavailable",
                    "weighting_agreement": "unavailable",
                }
            )
            continue
        gbp = currencies["GBP"]
        usd = currencies["USD"]
        assert gbp is not None
        assert usd is not None
        primary_relative = int(gbp["gbp_score"]) - int(usd["usd_score"])
        weighted_relative = int(gbp["gbp_weighted_score"]) - int(
            usd["usd_weighted_score"]
        )
        primary_bias = _bias(
            primary_relative, config.scoring.primary_bias_threshold
        )
        weighted_bias = _bias(
            weighted_relative, config.scoring.weighted_bias_threshold
        )
        agreement = (
            "agree"
            if primary_bias == weighted_bias
            else "neutral_mismatch"
            if primary_bias == 0 or weighted_bias == 0
            else "disagree"
        )
        rows.append(
            {
                "fundamental_available": True,
                "fundamental_model": "relative_strength_v1",
                "fundamental_unavailable_reason": None,
                **gbp,
                **usd,
                "fundamental_relative_score": primary_relative,
                "fundamental_bias": primary_bias,
                "fundamental_bias_label": _label(primary_bias),
                "weighted_fundamental_relative_score": weighted_relative,
                "weighted_fundamental_bias": weighted_bias,
                "weighted_fundamental_bias_label": _label(weighted_bias),
                "weighting_agreement": agreement,
            }
        )
    return pd.concat([events.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
