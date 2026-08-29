from pathlib import Path

import pandas as pd

import gbpusd_research.research.phase3 as phase3
from gbpusd_research.config import (
    load_fundamental_bias_config,
    load_project_config,
    load_value_state_config,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _final_events() -> pd.DataFrame:
    opened = pd.to_datetime(["2024-01-05 08:00:00Z", "2024-01-05 13:00:00Z"])
    return pd.DataFrame(
        {
            "event_id": ["london", "new-york"],
            "event_timestamp_utc": opened,
            "session_name": ["london", "new_york"],
            "calendar_month": [1, 1],
            "value_eligible": [True, True],
            "fundamental_available": [True, True],
            "fundamental_eligible": [True, True],
            "fundamental_bias": [-1, -1],
            "fundamental_bias_label": ["short", "short"],
            "fundamental_exclusion_reason": [None, None],
            "policy_value_relation": ["supports_reversion", "opposes_reversion"],
            "fundamental_fwd_60_bias_aligned_return_pips": [1.0, -1.0],
            "fundamental_fwd_60_reversion_aligned_return_pips": [1.0, -1.0],
        }
    )


def test_synthetic_phase3_run_writes_report_contract(
    tmp_path: Path, monkeypatch
) -> None:
    config = load_project_config(
        PROJECT_ROOT / "config/research.yaml",
        PROJECT_ROOT / "config/sessions.yaml",
    )
    value_config = load_value_state_config(PROJECT_ROOT / "config/value_state.yaml")
    fundamental_config = load_fundamental_bias_config(
        PROJECT_ROOT / "config/fundamental_bias.yaml"
    )
    events = _final_events()
    policy = pd.DataFrame(
        {
            "event_id": ["gbp", "usd"],
            "currency": ["GBP", "USD"],
            "available_at_utc": pd.to_datetime(
                ["2023-01-01 00:00:00Z", "2023-01-01 00:00:00Z"]
            ),
            "rate_mid_pct": [5.0, 5.25],
        }
    )
    comparisons = pd.DataFrame(
        {
            "contrast": ["bias_aligned_return_vs_zero"],
            "session_name": ["london"],
            "horizon_minutes": [60],
            "first_count": [30],
            "second_count": [0],
            "first_mean": [3.0],
            "second_mean": [0.0],
            "mean_difference": [3.0],
            "ci_low": [1.0],
            "ci_high": [5.0],
        }
    )
    gate = {
        "passed": True,
        "checks": {
            "feature_coverage": True,
            "point_in_time_invariants": True,
            "minimum_primary_group_size": True,
            "direction_month_breadth": True,
            "material_fundamental_contrast": True,
        },
        "feature_coverage_ratio": 1.0,
    }

    monkeypatch.setattr(
        phase3,
        "_load_phase2_events",
        lambda *args: (
            events,
            tmp_path / "phase2",
            {"run_id": "synthetic-phase2", "development_gate": {"passed": True}},
        ),
    )
    monkeypatch.setattr(phase3, "load_policy_rate_events", lambda *args: policy)
    monkeypatch.setattr(phase3, "attach_policy_bias", lambda *args, **kwargs: events)
    monkeypatch.setattr(
        phase3, "attach_fundamental_outcomes", lambda *args, **kwargs: events
    )
    monkeypatch.setattr(
        phase3,
        "fundamental_conditional_statistics",
        lambda *args, **kwargs: pd.DataFrame({"grouping": ["bias"]}),
    )
    monkeypatch.setattr(
        phase3, "fundamental_comparisons", lambda *args, **kwargs: comparisons
    )
    monkeypatch.setattr(phase3, "_evaluate_gate", lambda *args: gate)
    monkeypatch.setattr(phase3, "_sha256_file", lambda *args: "synthetic-sha256")

    def fake_figures(
        _events: pd.DataFrame, _policy: pd.DataFrame, output: Path
    ) -> list[Path]:
        output.mkdir(parents=True)
        paths = []
        for name in (
            "bias_counts.png",
            "aligned_return_by_horizon.png",
            "value_reversion_interaction.png",
            "policy_timeline.png",
        ):
            path = output / name
            path.write_bytes(b"synthetic")
            paths.append(path)
        return paths

    monkeypatch.setattr(phase3, "_create_figures", fake_figures)

    result = phase3.run_phase3(tmp_path, config, value_config, fundamental_config)
    output = tmp_path / result["output_directory"]

    for relative in (
        "run_manifest.json",
        "data_quality.json",
        "policy_timeline.csv",
        "fundamental_events.parquet",
        "event_exclusions.parquet",
        "conditional_statistics.csv",
        "statistical_comparisons.csv",
        "report.md",
        "figures/bias_counts.png",
        "figures/aligned_return_by_horizon.png",
        "figures/value_reversion_interaction.png",
        "figures/policy_timeline.png",
    ):
        assert (output / relative).is_file()
    assert result["development_gate"]["passed"]


def test_gate_rejects_direction_with_insufficient_month_breadth() -> None:
    config = load_fundamental_bias_config(PROJECT_ROOT / "config/fundamental_bias.yaml")
    opened = pd.to_datetime(["2024-01-05 08:00:00Z", "2024-03-05 08:00:00Z"])
    events = pd.DataFrame(
        {
            "event_timestamp_utc": opened,
            "calendar_month": [1, 3],
            "value_eligible": [True, True],
            "fundamental_eligible": [True, True],
            "fundamental_bias_label": ["long", "short"],
            "fundamental_bias": [1, -1],
            "gbp_policy_available_at": opened - pd.to_timedelta(1, unit="D"),
            "usd_policy_available_at": opened - pd.to_timedelta(1, unit="D"),
            "gbp_lookback_available_at": opened - pd.to_timedelta(100, unit="D"),
            "usd_lookback_available_at": opened - pd.to_timedelta(100, unit="D"),
            "policy_carry_signal": [1, -1],
            "policy_impulse_signal": [0, 0],
            "policy_relative_score": [1, -1],
        }
    )
    policy = pd.DataFrame({"event_id": ["gbp", "usd"]})
    comparisons = pd.DataFrame(
        {
            "contrast": ["bias_aligned_return_vs_zero"],
            "session_name": ["london"],
            "horizon_minutes": [60],
            "first_count": [30],
            "second_count": [0],
            "mean_difference": [3.0],
            "ci_low": [1.0],
            "ci_high": [5.0],
        }
    )

    gate = phase3._evaluate_gate(events, policy, comparisons, config)

    assert not gate["checks"]["direction_month_breadth"]
    assert not gate["passed"]
