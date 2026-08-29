from pathlib import Path

import pandas as pd

from gbpusd_research.config import load_fundamental_bias_config
from gbpusd_research.data.macro import load_policy_rate_events
from gbpusd_research.features.fundamentals import attach_policy_bias

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _policy_events() -> pd.DataFrame:
    config = load_fundamental_bias_config(PROJECT_ROOT / "config/fundamental_bias.yaml")
    return load_policy_rate_events(PROJECT_ROOT, config)


def test_boe_decision_is_available_at_exact_publication_timestamp() -> None:
    events = pd.DataFrame(
        {
            "event_timestamp_utc": pd.to_datetime(
                ["2024-08-01 10:59:59Z", "2024-08-01 11:00:00Z"]
            )
        }
    )

    result = attach_policy_bias(events, _policy_events(), impulse_lookback_days=90)

    assert result.loc[0, "gbp_policy_rate_pct"] == 5.25
    assert result.loc[0, "policy_relative_score"] == -1
    assert result.loc[1, "gbp_policy_rate_pct"] == 5.00
    assert result.loc[1, "policy_relative_score"] == -2
    assert result.loc[1, "fundamental_bias_label"] == "short"


def test_future_policy_sentinel_cannot_change_earlier_bias() -> None:
    event = pd.DataFrame(
        {"event_timestamp_utc": pd.to_datetime(["2024-08-01 10:00:00Z"])}
    )
    policy = _policy_events()
    sentinel = policy.iloc[[-1]].copy()
    sentinel["event_id"] = "future-sentinel"
    sentinel["currency"] = "GBP"
    sentinel["available_at_utc"] = pd.Timestamp("2024-08-01 10:00:01Z")
    sentinel["rate_lower_pct"] = 9.0
    sentinel["rate_upper_pct"] = 9.0
    sentinel["rate_mid_pct"] = 9.0

    baseline = attach_policy_bias(event, policy, impulse_lookback_days=90)
    with_future = attach_policy_bias(
        event,
        pd.concat([policy, sentinel], ignore_index=True).sort_values(
            "available_at_utc"
        ),
        impulse_lookback_days=90,
    )

    assert (
        baseline.loc[0, "policy_relative_score"]
        == with_future.loc[0, "policy_relative_score"]
    )
    assert (
        baseline.loc[0, "gbp_policy_rate_pct"]
        == with_future.loc[0, "gbp_policy_rate_pct"]
    )


def test_exact_zero_score_maps_to_neutral() -> None:
    policy = pd.DataFrame(
        {
            "event_id": ["gbp", "usd"],
            "currency": ["GBP", "USD"],
            "available_at_utc": pd.to_datetime(
                ["2023-01-01 00:00:00Z", "2023-01-01 00:00:00Z"]
            ),
            "rate_mid_pct": [5.0, 5.0],
        }
    )
    event = pd.DataFrame(
        {"event_timestamp_utc": pd.to_datetime(["2024-01-01 08:00:00Z"])}
    )

    result = attach_policy_bias(event, policy, impulse_lookback_days=90).iloc[0]

    assert result["policy_relative_score"] == 0
    assert result["fundamental_bias"] == 0
    assert result["fundamental_bias_label"] == "neutral"
