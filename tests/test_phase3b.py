from pathlib import Path

import pandas as pd

import gbpusd_research.research.phase3b as phase3b
from gbpusd_research.config import (
    load_fundamental_strength_config,
    load_project_config,
    load_value_state_config,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _final_events() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_timestamp_utc": pd.to_datetime(
                ["2024-01-05 08:00:00Z", "2024-01-05 13:00:00Z"]
            ),
            "session_name": ["london", "new_york"],
            "calendar_month": [1, 1],
            "value_eligible": [True, True],
            "fundamental_available": [True, True],
            "fundamental_eligible": [True, True],
            "fundamental_bias": [-1, 1],
            "fundamental_bias_label": ["short", "long"],
            "fundamental_exclusion_reason": [None, None],
            "weighting_agreement": ["agree", "neutral_mismatch"],
            "fundamental_value_relation": [
                "supports_reversion",
                "opposes_reversion",
            ],
            "fundamental_fwd_60_bias_aligned_return_pips": [1.0, -1.0],
            "fundamental_fwd_60_reversion_aligned_return_pips": [1.0, -1.0],
        }
    )


def test_synthetic_phase3b_run_writes_report_contract(
    tmp_path: Path, monkeypatch
) -> None:
    config = load_project_config(
        PROJECT_ROOT / "config/research.yaml",
        PROJECT_ROOT / "config/sessions.yaml",
    )
    value_config = load_value_state_config(PROJECT_ROOT / "config/value_state.yaml")
    strength_config = load_fundamental_strength_config(
        PROJECT_ROOT / "config/fundamental_strength.yaml"
    )
    events = _final_events()
    policy = pd.DataFrame({"event_id": ["gbp", "usd"]})
    macro = pd.DataFrame({"event_id": ["gbp-cpi", "usd-cpi"]})
    yields = pd.DataFrame(
        {
            "currency": ["GBP", "USD"],
            "observation_date": pd.to_datetime(["2024-01-04", "2024-01-04"]),
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
            "minimum_direction_group_size": True,
            "direction_month_breadth": True,
            "material_primary_contrast": True,
        },
        "feature_coverage_ratio": 1.0,
    }

    monkeypatch.setattr(
        phase3b,
        "_load_phase2_events",
        lambda *args: (
            events,
            tmp_path / "phase2",
            {"run_id": "synthetic-phase2", "development_gate": {"passed": True}},
        ),
    )
    monkeypatch.setattr(
        phase3b, "load_strength_policy_rate_events", lambda *args: policy
    )
    monkeypatch.setattr(phase3b, "load_macro_release_events", lambda *args: macro)
    monkeypatch.setattr(phase3b, "load_two_year_yields", lambda *args: yields)
    monkeypatch.setattr(
        phase3b, "attach_relative_fundamental_strength", lambda *args: events
    )
    monkeypatch.setattr(
        phase3b, "attach_fundamental_outcomes", lambda *args, **kwargs: events
    )
    monkeypatch.setattr(
        phase3b,
        "_attach_baseline_and_secondary_outcomes",
        lambda *args: events,
    )
    monkeypatch.setattr(
        phase3b,
        "fundamental_conditional_statistics",
        lambda *args, **kwargs: pd.DataFrame({"grouping": ["bias"]}),
    )
    monkeypatch.setattr(
        phase3b, "fundamental_comparisons", lambda *args, **kwargs: comparisons
    )
    monkeypatch.setattr(
        phase3b,
        "_incremental_comparisons",
        lambda *args, **kwargs: pd.DataFrame(columns=comparisons.columns),
    )
    monkeypatch.setattr(
        phase3b,
        "_sensitivity_statistics",
        lambda *args: pd.DataFrame({"statistic": ["agreement"]}),
    )
    monkeypatch.setattr(phase3b, "_evaluate_gate", lambda *args: gate)
    monkeypatch.setattr(phase3b, "_sha256_file", lambda *args: "synthetic-sha256")

    def fake_figures(_events: pd.DataFrame, output: Path) -> list[Path]:
        output.mkdir(parents=True)
        paths = []
        for name in (
            "bias_counts.png",
            "relative_score_timeline.png",
            "aligned_return_by_horizon.png",
            "weighting_agreement.png",
            "value_reversion_interaction.png",
        ):
            path = output / name
            path.write_bytes(b"synthetic")
            paths.append(path)
        return paths

    monkeypatch.setattr(phase3b, "_create_figures", fake_figures)

    result = phase3b.run_phase3b(
        tmp_path, config, value_config, strength_config
    )
    output = tmp_path / result["output_directory"]

    for relative in (
        "run_manifest.json",
        "data_quality.json",
        "macro_timeline.csv",
        "yield_timeline.csv",
        "session_bias.parquet",
        "event_exclusions.parquet",
        "conditional_statistics.csv",
        "statistical_comparisons.csv",
        "sensitivity_statistics.csv",
        "report.md",
        "figures/bias_counts.png",
        "figures/relative_score_timeline.png",
        "figures/aligned_return_by_horizon.png",
        "figures/weighting_agreement.png",
        "figures/value_reversion_interaction.png",
    ):
        assert (output / relative).is_file()
    assert result["development_gate"]["passed"]


def test_incremental_bootstrap_is_deterministic() -> None:
    rows = []
    for index in range(40):
        rows.append(
            {
                "session_name": "london",
                "fundamental_eligible": True,
                "fwd_60_primary_minus_policy_v1_pips": float(index % 3 - 1),
            }
        )
    events = pd.DataFrame(rows)
    arguments = {
        "horizons": (60,),
        "resamples": 200,
        "confidence_level": 0.95,
        "random_seed": 42,
    }

    first = phase3b._incremental_comparisons(events, **arguments)
    second = phase3b._incremental_comparisons(events, **arguments)

    pd.testing.assert_frame_equal(first, second)
