from pathlib import Path

import pandas as pd

from gbpusd_research.config import load_fundamental_strength_config
from gbpusd_research.data.macro import (
    load_macro_release_events,
    load_strength_policy_rate_events,
    load_two_year_yields,
)
from gbpusd_research.features.fundamental_strength import (
    attach_relative_fundamental_strength,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _inputs():
    config = load_fundamental_strength_config(
        PROJECT_ROOT / "config/fundamental_strength.yaml"
    )
    return (
        config,
        load_strength_policy_rate_events(PROJECT_ROOT, config),
        load_macro_release_events(PROJECT_ROOT, config),
        load_two_year_yields(PROJECT_ROOT, config),
    )


def test_us_release_after_new_york_open_is_not_available_at_open() -> None:
    config, policy, macro, yields = _inputs()
    events = pd.DataFrame(
        {
            "event_timestamp_utc": pd.to_datetime(
                ["2024-08-14 12:00:00Z", "2024-08-14 12:30:00Z"]
            )
        }
    )

    result = attach_relative_fundamental_strength(
        events, policy, macro, yields, config
    )

    assert result.loc[0, "usd_headline_current_event_id"].endswith("2024-06")
    assert result.loc[1, "usd_headline_current_event_id"].endswith("2024-07")
    assert result.loc[0, "usd_headline_available_at"] < events.loc[
        0, "event_timestamp_utc"
    ]
    assert result.loc[1, "usd_headline_available_at"] == events.loc[
        1, "event_timestamp_utc"
    ]


def test_equal_and_weighted_scores_follow_frozen_arithmetic() -> None:
    config, policy, macro, yields = _inputs()
    events = pd.DataFrame(
        {"event_timestamp_utc": pd.to_datetime(["2024-10-16 12:00:00Z"])}
    )

    row = attach_relative_fundamental_strength(
        events, policy, macro, yields, config
    ).iloc[0]

    for currency in ("gbp", "usd"):
        assert row[f"{currency}_score"] == sum(
            row[f"{currency}_{column}"]
            for column in (
                "policy_score",
                "inflation_score",
                "labor_score",
                "yield_expectation_score",
            )
        )
    assert row["fundamental_relative_score"] == row["gbp_score"] - row["usd_score"]
    assert row["weighted_fundamental_relative_score"] == (
        row["gbp_weighted_score"] - row["usd_weighted_score"]
    )


def test_future_macro_sentinel_cannot_change_earlier_score() -> None:
    config, policy, macro, yields = _inputs()
    event = pd.DataFrame(
        {"event_timestamp_utc": pd.to_datetime(["2024-08-14 12:00:00Z"])}
    )
    sentinel = macro[macro["event_id"].eq("usd-headline_cpi_yoy-2024-07")].copy()
    sentinel["event_id"] = "future-sentinel"
    sentinel["available_at_utc"] = pd.Timestamp("2024-08-14 12:00:01Z")
    sentinel["value"] = 99.0

    baseline = attach_relative_fundamental_strength(
        event, policy, macro, yields, config
    )
    with_future = attach_relative_fundamental_strength(
        event,
        policy,
        pd.concat([macro, sentinel], ignore_index=True).sort_values(
            "available_at_utc", kind="stable"
        ),
        yields,
        config,
    )

    assert baseline.loc[0, "fundamental_relative_score"] == with_future.loc[
        0, "fundamental_relative_score"
    ]
    assert baseline.loc[0, "usd_headline_cpi_yoy"] == with_future.loc[
        0, "usd_headline_cpi_yoy"
    ]


def test_same_day_yield_sentinel_is_not_used() -> None:
    config, policy, macro, yields = _inputs()
    event = pd.DataFrame(
        {"event_timestamp_utc": pd.to_datetime(["2024-08-14 12:00:00Z"])}
    )
    sentinel = yields[yields["currency"].eq("USD")].iloc[[-1]].copy()
    sentinel["observation_date"] = pd.Timestamp("2024-08-14")
    sentinel["available_at_utc"] = pd.Timestamp("2024-08-14 11:59:00Z")
    sentinel["yield_2y_pct"] = 99.0

    baseline = attach_relative_fundamental_strength(
        event, policy, macro, yields, config
    )
    expanded = yields.copy()
    expanded.loc[len(expanded)] = sentinel.iloc[0]
    with_same_day = attach_relative_fundamental_strength(
        event,
        policy,
        macro,
        expanded.sort_values(
            ["currency", "observation_date"], kind="stable"
        ),
        config,
    )

    assert baseline.loc[0, "usd_yield_2y_pct"] == with_same_day.loc[
        0, "usd_yield_2y_pct"
    ]
