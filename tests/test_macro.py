from pathlib import Path

import pandas as pd
import pytest

from gbpusd_research.config import (
    load_fundamental_bias_config,
    load_fundamental_repricing_config,
    load_fundamental_strength_config,
)
from gbpusd_research.data.macro import (
    load_macro_release_events,
    load_policy_decision_events,
    load_policy_rate_events,
    load_repricing_two_year_yields,
    load_strength_policy_rate_events,
    load_two_year_yields,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_policy_ledger_is_valid_and_uses_official_sources() -> None:
    config = load_fundamental_bias_config(PROJECT_ROOT / "config/fundamental_bias.yaml")

    events = load_policy_rate_events(PROJECT_ROOT, config)

    assert len(events) == 7
    assert set(events["currency"]) == {"GBP", "USD"}
    assert events["event_id"].is_unique
    assert (
        events.loc[events["event_id"].eq("fed-2023-07-26"), "rate_mid_pct"].iat[0]
        == 5.375
    )
    assert events["source_url"].str.startswith("https://").all()


def test_policy_ledger_rejects_inverted_rate_range(tmp_path: Path) -> None:
    config = load_fundamental_bias_config(PROJECT_ROOT / "config/fundamental_bias.yaml")
    source = pd.read_csv(PROJECT_ROOT / config.policy.events_path)
    source.loc[0, "rate_lower_pct"] = 6.0
    path = tmp_path / "invalid-policy.csv"
    source.to_csv(path, index=False)
    invalid = config.model_copy(
        update={"policy": config.policy.model_copy(update={"events_path": path})}
    )

    with pytest.raises(ValueError, match="lower bound"):
        load_policy_rate_events(tmp_path, invalid)


def test_checked_in_phase3b_ledgers_are_complete_and_official() -> None:
    config = load_fundamental_strength_config(
        PROJECT_ROOT / "config/fundamental_strength.yaml"
    )

    policy = load_strength_policy_rate_events(PROJECT_ROOT, config)
    macro = load_macro_release_events(PROJECT_ROOT, config)
    yields = load_two_year_yields(PROJECT_ROOT, config)

    assert len(policy) == 9
    assert len(macro) == 84
    assert len(yields) == 586
    assert macro["event_id"].is_unique
    assert set(macro["pillar"]) == {"inflation", "labor"}
    assert macro["source_url"].str.startswith("https://").all()
    assert not yields.duplicated(["currency", "observation_date"]).any()
    assert yields["source_url"].str.startswith("https://").all()


def test_macro_ledger_rejects_current_history_indicator(tmp_path: Path) -> None:
    config = load_fundamental_strength_config(
        PROJECT_ROOT / "config/fundamental_strength.yaml"
    )
    source = pd.read_csv(PROJECT_ROOT / config.data.macro_events_path)
    source.loc[0, "indicator"] = "revised_current_history"
    path = tmp_path / "invalid-macro.csv"
    source.to_csv(path, index=False)
    invalid = config.model_copy(
        update={
            "data": config.data.model_copy(update={"macro_events_path": path})
        }
    )

    with pytest.raises(ValueError, match="indicators do not match"):
        load_macro_release_events(tmp_path, invalid)


def test_checked_in_phase3c_policy_decisions_are_complete() -> None:
    config = load_fundamental_repricing_config(
        PROJECT_ROOT / "config/fundamental_repricing.yaml"
    )

    policy = load_policy_decision_events(PROJECT_ROOT, config)
    yields = load_repricing_two_year_yields(PROJECT_ROOT, config)

    assert len(policy) == 16
    assert policy.groupby("currency").size().to_dict() == {"GBP": 8, "USD": 8}
    assert policy["source_url"].str.startswith("https://").all()
    assert len(yields) == 586
