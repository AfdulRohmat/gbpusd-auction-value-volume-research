from pathlib import Path

import pandas as pd

from gbpusd_research.config import load_fundamental_repricing_config
from gbpusd_research.data.macro import (
    load_macro_release_events,
    load_policy_decision_events,
    load_repricing_two_year_yields,
)
from gbpusd_research.features.fundamental_repricing import (
    attach_relative_repricing_bias,
    build_catalyst_yield_shocks,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _inputs():
    config = load_fundamental_repricing_config(
        PROJECT_ROOT / "config/fundamental_repricing.yaml"
    )
    policy = load_policy_decision_events(PROJECT_ROOT, config)
    macro = load_macro_release_events(PROJECT_ROOT, config)
    yields = load_repricing_two_year_yields(PROJECT_ROOT, config)
    shocks = build_catalyst_yield_shocks(
        policy,
        macro,
        yields,
        start=pd.Timestamp("2024-01-01").date(),
        end=pd.Timestamp("2025-01-01").date(),
    )
    return config, policy, macro, yields, shocks


def test_registered_catalysts_bundle_to_64_same_day_yield_shocks() -> None:
    _, _, _, _, shocks = _inputs()

    assert len(shocks) == 64
    assert shocks["catalyst_id"].is_unique
    assert shocks["yield_mapping_available"].all()
    assert shocks.groupby(["currency", "pillar"]).size().to_dict() == {
        ("GBP", "inflation"): 12,
        ("GBP", "labor"): 12,
        ("GBP", "policy"): 8,
        ("USD", "inflation"): 12,
        ("USD", "labor"): 12,
        ("USD", "policy"): 8,
    }
    assert shocks["shock_available_at_utc"].ge(shocks["release_at_utc"]).all()


def test_boe_shock_waits_for_official_yield_publication() -> None:
    config, _, _, yields, shocks = _inputs()
    events = pd.DataFrame(
        {
            "event_timestamp_utc": pd.to_datetime(
                ["2024-08-02 10:59:00Z", "2024-08-02 11:00:00Z"]
            )
        }
    )

    result = attach_relative_repricing_bias(events, shocks, yields, config)

    assert result.loc[0, "gbp_catalyst_id"] != "boe-decision-2024-08-01"
    assert result.loc[1, "gbp_catalyst_id"] == "boe-decision-2024-08-01"
    assert result.loc[1, "gbp_shock_available_at"] == events.loc[
        1, "event_timestamp_utc"
    ]


def test_relative_bias_follows_frozen_five_basis_point_threshold() -> None:
    config, _, _, yields, shocks = _inputs()
    events = pd.DataFrame(
        {"event_timestamp_utc": pd.to_datetime(["2024-08-02 12:00:00Z"])}
    )

    row = attach_relative_repricing_bias(events, shocks, yields, config).iloc[0]
    relative = row["gbp_yield_shock_bps"] - row["usd_yield_shock_bps"]
    expected = 1 if relative >= 5 else -1 if relative <= -5 else 0

    assert row["repricing_relative_shock_bps"] == relative
    assert row["repricing_bias"] == expected


def test_future_shock_sentinel_cannot_change_earlier_bias() -> None:
    config, _, _, yields, shocks = _inputs()
    events = pd.DataFrame(
        {"event_timestamp_utc": pd.to_datetime(["2024-08-02 12:00:00Z"])}
    )
    sentinel = shocks.iloc[[-1]].copy()
    sentinel["catalyst_id"] = "future-sentinel"
    sentinel["currency"] = "GBP"
    sentinel["release_at_utc"] = pd.Timestamp("2024-08-02 12:00:01Z")
    sentinel["shock_available_at_utc"] = pd.Timestamp("2024-08-02 12:00:01Z")
    sentinel["yield_shock_bps"] = 999.0

    baseline = attach_relative_repricing_bias(events, shocks, yields, config)
    expanded = attach_relative_repricing_bias(
        events,
        pd.concat([shocks, sentinel], ignore_index=True),
        yields,
        config,
    )

    assert baseline.loc[0, "repricing_relative_shock_bps"] == expanded.loc[
        0, "repricing_relative_shock_bps"
    ]
    assert baseline.loc[0, "repricing_bias"] == expanded.loc[0, "repricing_bias"]
