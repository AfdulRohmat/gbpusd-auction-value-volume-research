from pathlib import Path

import pytest
from pydantic import ValidationError

from gbpusd_research.config import (
    ResearchConfig,
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


def test_two_year_configuration_has_exclusive_2025_end() -> None:
    config = load_project_config(
        PROJECT_ROOT / "config/research_2023_2024.yaml",
        PROJECT_ROOT / "config/sessions.yaml",
    )

    assert config.research.data.start.isoformat() == "2023-01-01"
    assert config.research.data.end.isoformat() == "2025-01-01"
    assert (config.research.data.end - config.research.data.start).days == 731


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
