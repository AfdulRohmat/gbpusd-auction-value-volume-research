from pathlib import Path

import pandas as pd

from gbpusd_research.config import load_opening_value_strategy_config
from gbpusd_research.research.phase4 import (
    _artifact_records,
    evaluate_validation_gate,
    performance_statistics,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _config():
    return load_opening_value_strategy_config(
        PROJECT_ROOT / "config/opening_value_strategy.yaml"
    )


def test_artifact_records_excludes_manifest_itself(tmp_path: Path) -> None:
    (tmp_path / "result.csv").write_text("value\n1\n", encoding="utf-8")
    (tmp_path / "run_manifest.json").write_text("{}", encoding="utf-8")

    records = _artifact_records(tmp_path)

    assert [record["path"] for record in records] == ["result.csv"]


def _trade_rows(*, role: str, session: str, count: int, r_value: float) -> list[dict]:
    rows = []
    for index in range(count):
        direction = 1 if index % 2 == 0 else -1
        rows.append(
            {
                "sample_role": role,
                "session_name": session,
                "event_id": f"{role}-{session}-{index}",
                "local_session_date": pd.Timestamp(
                    2025 if role == "validation" else 2024,
                    index % 12 + 1,
                    1,
                ).date(),
                "phase1_eligible": True,
                "value_eligible": True,
                "candidate": True,
                "signal_found": True,
                "trade_executed": True,
                "direction": direction,
                "entry_timestamp_utc": pd.Timestamp("2025-01-01", tz="UTC")
                + pd.to_timedelta(index, unit="D"),
                "pnl_pips": r_value * 10,
                "r_multiple": r_value,
                "stressed_r_multiple": r_value - 0.08,
                "exit_reason": "target" if r_value > 0 else "stop",
                "initial_risk_pips": 10.0,
                "reward_to_risk": 1.5,
            }
        )
    return rows


def test_performance_statistics_keeps_samples_and_sessions_separate() -> None:
    events = pd.DataFrame(
        _trade_rows(role="development", session="new_york", count=12, r_value=-0.2)
        + _trade_rows(role="validation", session="new_york", count=36, r_value=0.3)
        + _trade_rows(role="validation", session="london", count=36, r_value=-0.5)
    )
    first = performance_statistics(events, _config(), random_seed=20250301)
    second = performance_statistics(events, _config(), random_seed=20250301)

    pd.testing.assert_frame_equal(first, second)
    assert len(first) == 3
    primary = first[
        first["sample_role"].eq("validation")
        & first["session_name"].eq("new_york")
    ].iloc[0]
    assert primary["trades"] == 36
    assert primary["active_months"] == 12
    assert primary["mean_r_ci_low"] > 0


def test_performance_statistics_handles_a_session_with_no_trades() -> None:
    events = pd.DataFrame(
        {
            "sample_role": ["validation"],
            "session_name": ["new_york"],
            "phase1_eligible": [True],
            "value_eligible": [True],
            "candidate": [False],
            "signal_found": [False],
            "trade_executed": [False],
            "direction": [float("nan")],
            "local_session_date": [pd.Timestamp("2025-01-02").date()],
            "entry_timestamp_utc": [pd.NaT],
            "pnl_pips": [float("nan")],
            "r_multiple": [float("nan")],
            "stressed_r_multiple": [float("nan")],
            "exit_reason": [None],
            "initial_risk_pips": [float("nan")],
            "reward_to_risk": [float("nan")],
        }
    )
    result = performance_statistics(events, _config(), random_seed=20250301)

    assert result.iloc[0]["trades"] == 0
    assert result.iloc[0]["active_months"] == 0


def test_validation_gate_uses_only_validation_new_york() -> None:
    statistics = pd.DataFrame(
        [
            {
                "sample_role": "validation",
                "session_name": "new_york",
                "trades": 40,
                "active_months": 10,
                "long_trades": 20,
                "short_trades": 20,
                "mean_r": 0.20,
                "mean_r_ci_low": 0.02,
                "profit_factor": 1.4,
                "maximum_drawdown_r": 4.0,
                "mean_stressed_r": 0.10,
            },
            {
                "sample_role": "validation",
                "session_name": "london",
                "trades": 100,
                "active_months": 12,
                "long_trades": 50,
                "short_trades": 50,
                "mean_r": -1.0,
                "mean_r_ci_low": -2.0,
                "profit_factor": 0.2,
                "maximum_drawdown_r": 50.0,
                "mean_stressed_r": -1.1,
            },
        ]
    )
    events = pd.DataFrame(
        {
            "phase1_eligible": [True] * 100,
            "value_eligible": [True] * 96 + [False] * 4,
        }
    )
    upstream = {
        "phase1": {"research_gate": {"development_passed": True}},
        "phase2": {"data_quality_valid": True},
    }
    gate = evaluate_validation_gate(
        statistics,
        events,
        upstream,
        {"passed": True},
        _config(),
    )

    assert gate["passed"]
    assert gate["authoritative_sample"] == "2025 new_york"


def test_strong_london_cannot_rescue_failed_new_york() -> None:
    statistics = pd.DataFrame(
        [
            {
                "sample_role": "validation",
                "session_name": "new_york",
                "trades": 40,
                "active_months": 10,
                "long_trades": 20,
                "short_trades": 20,
                "mean_r": -0.10,
                "mean_r_ci_low": -0.30,
                "profit_factor": 0.9,
                "maximum_drawdown_r": 4.0,
                "mean_stressed_r": -0.20,
            },
            {
                "sample_role": "validation",
                "session_name": "london",
                "trades": 100,
                "active_months": 12,
                "long_trades": 50,
                "short_trades": 50,
                "mean_r": 1.0,
                "mean_r_ci_low": 0.8,
                "profit_factor": 3.0,
                "maximum_drawdown_r": 2.0,
                "mean_stressed_r": 0.9,
            },
        ]
    )
    gate = evaluate_validation_gate(
        statistics,
        pd.DataFrame(
            {"phase1_eligible": [True] * 100, "value_eligible": [True] * 100}
        ),
        {
            "phase1": {"research_gate": {"development_passed": True}},
            "phase2": {"data_quality_valid": True},
        },
        {"passed": True},
        _config(),
    )

    assert not gate["passed"]
    assert not gate["checks"]["minimum_expectancy_r"]
