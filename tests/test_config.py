from pathlib import Path

import pytest
from pydantic import ValidationError

from gbpusd_research.config import (
    ResearchConfig,
    load_auction_taxonomy_config,
    load_balance_boundary_strategy_config,
    load_exness_quote_activity_config,
    load_fundamental_bias_config,
    load_fundamental_repricing_config,
    load_fundamental_strength_config,
    load_opening_auction_config,
    load_project_config,
    load_value_state_config,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_configuration_is_valid() -> None:
    config = load_project_config(
        PROJECT_ROOT / "config/research.yaml",
        PROJECT_ROOT / "config/sessions.yaml",
    )

    assert config.research.instrument.symbol == "GBPUSD"
    assert config.research.data.start.isoformat() == "2024-01-01"
    assert config.sessions.sessions["london"].timezone == "Europe/London"
    assert config.sessions.sessions["new_york"].open.isoformat() == "08:00:00"


def test_checked_in_value_state_configuration_is_valid() -> None:
    config = load_value_state_config(PROJECT_ROOT / "config/value_state.yaml")

    assert config.profile.bin_size_pips == 1.0
    assert config.profile.value_area_fraction == 0.70
    assert config.classification.transition_horizons_minutes == (15, 30, 60, 90)


def test_checked_in_fundamental_configuration_is_valid() -> None:
    config = load_fundamental_bias_config(PROJECT_ROOT / "config/fundamental_bias.yaml")

    assert config.policy.impulse_lookback_days == 90
    assert config.analysis.horizons_minutes == (15, 30, 60, 90)
    assert config.analysis.minimum_group_size == 30


def test_checked_in_relative_strength_configuration_is_valid() -> None:
    config = load_fundamental_strength_config(
        PROJECT_ROOT / "config/fundamental_strength.yaml"
    )

    assert config.scoring.primary_bias_threshold == 2
    assert config.scoring.yield_lookback_observations == 20
    assert config.scoring.robustness_weights.model_dump() == {
        "policy": 3,
        "inflation": 2,
        "labor": 2,
        "yield_expectation": 1,
    }


def test_checked_in_repricing_configuration_is_valid() -> None:
    config = load_fundamental_repricing_config(
        PROJECT_ROOT / "config/fundamental_repricing.yaml"
    )

    assert config.signal.active_yield_observations == 5
    assert config.signal.bias_threshold_bps == 5.0
    assert config.analysis.horizons_session_days == (1, 3, 5)
    assert config.analysis.primary_horizon_session_days == 3


def test_two_year_configuration_has_exclusive_2025_end() -> None:
    config = load_project_config(
        PROJECT_ROOT / "config/research_2023_2024.yaml",
        PROJECT_ROOT / "config/sessions.yaml",
    )

    assert config.research.data.start.isoformat() == "2023-01-01"
    assert config.research.data.end.isoformat() == "2025-01-01"
    assert (config.research.data.end - config.research.data.start).days == 731


def test_checked_in_opening_auction_configuration_is_valid() -> None:
    config = load_opening_auction_config(
        PROJECT_ROOT / "config/opening_auction_state_machine.yaml"
    )

    assert config.classification.observation_minutes == 15
    assert config.classification.imbalance_efficiency_threshold == 0.60
    assert config.execution.target_r_multiple == 2.0
    assert config.trailing.swing_bars == 3
    assert set(config.analysis.variants) == {
        "fixed_2r",
        "session_hold",
        "trailing_session",
    }


def test_checked_in_auction_taxonomy_configuration_is_valid() -> None:
    config = load_auction_taxonomy_config(
        PROJECT_ROOT / "config/auction_state_taxonomy.yaml"
    )

    assert config.state.window_minutes == 30
    assert config.state.confirmation_windows == 2
    assert config.activity.baseline_bars == 72
    assert config.transition.horizons_minutes == (15, 30, 60, 90)
    assert set(config.analysis.controls) == {"fixed", "matched"}


def test_checked_in_balance_boundary_configuration_is_valid() -> None:
    config = load_balance_boundary_strategy_config(
        PROJECT_ROOT / "config/balance_boundary_strategy.yaml"
    )

    assert config.context.signal_window_minutes == 90
    assert config.context.acceptance_consecutive_closes == 2
    assert config.execution.minimum_rotation_reward_to_risk == 1.5
    assert config.execution.breakout_target_r_multiple == 2.0
    assert set(config.analysis.portfolio_variants) == {
        "combined_fixed_2r",
        "combined_trailing_session",
    }


def test_checked_in_exness_quote_activity_configuration_is_valid() -> None:
    config = load_exness_quote_activity_config(
        PROJECT_ROOT / "config/exness_quote_activity.yaml"
    )

    assert config.data.source_preference == "mt5_account_export"
    assert config.data.accepted_symbols == ("GBPUSD", "GBPUSD-r")
    assert config.periods.development_start.isoformat() == "2024-01-01"
    assert config.periods.forward_end.isoformat() == "2026-08-01"
    assert config.features.observation_minutes == 15
    assert set(config.model.variants) == {
        "price_only",
        "activity_only",
        "price_activity",
    }
    assert config.execution.commission_pips_per_side == pytest.approx(0.35)
    assert config.gate.minimum_auc == 0.53


def test_research_config_rejects_unknown_keys() -> None:
    raw = {
        "instrument": {
            "symbol": "GBPUSD",
            "pip_size": 0.0001,
            "price_decimals": 5,
            "typo": True,
        },
        "data": {
            "source": "histdata",
            "raw_frequency": "tick",
            "output_frequency": "5min",
            "start": "2024-01-01",
            "end": "2024-02-01",
            "paths": {
                "raw": "data/raw",
                "interim": "data/interim",
                "processed": "data/processed",
            },
        },
        "quality": {
            "reject_crossed_quotes": True,
            "max_spread_pips_warning": 10,
            "event_min_coverage_ratio": 0.95,
            "exclude_weekends": True,
        },
        "study": {
            "horizons_minutes": [5],
            "preopen_windows_minutes": [30],
            "random_seed": 1,
            "bootstrap_resamples": 100,
            "confidence_level": 0.95,
        },
    }

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ResearchConfig.model_validate(raw)


def test_end_date_is_exclusive_and_must_follow_start() -> None:
    path = PROJECT_ROOT / "config/research.yaml"
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["data"]["end"] = raw["data"]["start"]

    with pytest.raises(ValidationError, match=r"data\.end must be later"):
        ResearchConfig.model_validate(raw)
