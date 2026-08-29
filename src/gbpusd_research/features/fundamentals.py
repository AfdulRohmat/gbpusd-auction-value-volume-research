"""Point-in-time monetary-policy bias features."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd


def _latest_at(
    events: pd.DataFrame, currency: str, timestamp: pd.Timestamp
) -> pd.Series | None:
    available = events[
        events["currency"].eq(currency) & events["available_at_utc"].le(timestamp)
    ]
    return None if available.empty else available.iloc[-1]


def _signal(value: float) -> int:
    return int(np.sign(value))


def attach_policy_bias(
    events: pd.DataFrame,
    policy_events: pd.DataFrame,
    *,
    impulse_lookback_days: int,
) -> pd.DataFrame:
    """Attach the frozen policy_bias_v1 score available at each event."""

    if impulse_lookback_days < 1:
        raise ValueError("impulse_lookback_days must be positive")
    output = events.copy()
    rows = []
    for event in output.itertuples(index=False):
        opened = pd.Timestamp(event.event_timestamp_utc)
        lookback = opened - timedelta(days=impulse_lookback_days)
        current = {
            currency: _latest_at(policy_events, currency, opened)
            for currency in ("GBP", "USD")
        }
        previous = {
            currency: _latest_at(policy_events, currency, lookback)
            for currency in ("GBP", "USD")
        }
        if any(value is None for value in (*current.values(), *previous.values())):
            rows.append(
                {
                    "fundamental_available": False,
                    "fundamental_model": "policy_bias_v1",
                    "fundamental_bias": np.nan,
                    "fundamental_bias_label": "unavailable",
                }
            )
            continue
        gbp_current = current["GBP"]
        usd_current = current["USD"]
        gbp_previous = previous["GBP"]
        usd_previous = previous["USD"]
        assert gbp_current is not None
        assert usd_current is not None
        assert gbp_previous is not None
        assert usd_previous is not None
        gbp_rate = float(gbp_current["rate_mid_pct"])
        usd_rate = float(usd_current["rate_mid_pct"])
        gbp_lookback_rate = float(gbp_previous["rate_mid_pct"])
        usd_lookback_rate = float(usd_previous["rate_mid_pct"])
        carry_spread = gbp_rate - usd_rate
        gbp_impulse = gbp_rate - gbp_lookback_rate
        usd_impulse = usd_rate - usd_lookback_rate
        impulse_spread = gbp_impulse - usd_impulse
        carry_signal = _signal(carry_spread)
        impulse_signal = _signal(impulse_spread)
        relative_score = carry_signal + impulse_signal
        bias = _signal(relative_score)
        rows.append(
            {
                "fundamental_available": True,
                "fundamental_model": "policy_bias_v1",
                "gbp_policy_event_id": gbp_current["event_id"],
                "usd_policy_event_id": usd_current["event_id"],
                "gbp_lookback_event_id": gbp_previous["event_id"],
                "usd_lookback_event_id": usd_previous["event_id"],
                "gbp_policy_available_at": gbp_current["available_at_utc"],
                "usd_policy_available_at": usd_current["available_at_utc"],
                "gbp_lookback_available_at": gbp_previous["available_at_utc"],
                "usd_lookback_available_at": usd_previous["available_at_utc"],
                "gbp_policy_rate_pct": gbp_rate,
                "usd_policy_rate_pct": usd_rate,
                "gbp_lookback_rate_pct": gbp_lookback_rate,
                "usd_lookback_rate_pct": usd_lookback_rate,
                "gbp_policy_impulse_pct": gbp_impulse,
                "usd_policy_impulse_pct": usd_impulse,
                "policy_carry_spread_pct": carry_spread,
                "policy_impulse_spread_pct": impulse_spread,
                "policy_carry_signal": carry_signal,
                "policy_impulse_signal": impulse_signal,
                "policy_relative_score": relative_score,
                "fundamental_bias": bias,
                "fundamental_bias_label": (
                    "long" if bias > 0 else "short" if bias < 0 else "neutral"
                ),
            }
        )
    return pd.concat([output.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
