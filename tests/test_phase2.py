from datetime import date
from pathlib import Path

import pandas as pd

import gbpusd_research.research.phase2 as phase2
from gbpusd_research.config import load_project_config, load_value_state_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _synthetic_events() -> pd.DataFrame:
    sessions = ["london", "london", "new_york", "new_york"]
    states = ["above_value", "inside_value", "above_value", "inside_value"]
    opens = [1.1020, 1.0950, 1.1030, 1.0960]
    opened = pd.to_datetime(
        [
            "2024-01-05 08:00:00Z",
            "2024-01-05 08:00:00Z",
            "2024-01-05 13:00:00Z",
            "2024-01-05 13:00:00Z",
        ]
    )
    frame = pd.DataFrame(
        {
            "event_id": [f"event-{index}" for index in range(4)],
            "session_name": sessions,
            "event_timestamp_utc": opened,
            "fx_trading_day": [date(2024, 1, 5)] * 4,
            "open_price_mid": opens,
            "eligible": [True] * 4,
            "value_eligible": [True] * 4,
            "profile_available": [True] * 4,
            "previous_profile_day": [date(2024, 1, 4)] * 4,
            "previous_poc": [1.0950] * 4,
            "previous_val": [1.0900] * 4,
            "previous_vah": [1.1000] * 4,
            "value_state": states,
            "vwap_available": [True] * 4,
            "vwap_available_at": opened - pd.to_timedelta(5, unit="min"),
            "vwap_slope_pips": [1.0, -1.0, 1.0, -1.0],
            "vwap_distance_pips": [5.0, -2.0, 6.0, -1.0],
            "vwap_zscore": [1.0, -0.5, 1.2, -0.2],
            "fwd_5_spread_median_pips": [0.8] * 4,
            "pre_30_spread_median_pips": [0.7] * 4,
            "fwd_60_range_pips": [20.0, 15.0, 22.0, 16.0],
            "fwd_60_return_pips": [4.0, -1.0, 5.0, -2.0],
            "value_fwd_60_reentered": [False, None, True, None],
            "value_fwd_60_acceptance_above": [True, False, True, False],
            "value_fwd_60_acceptance_below": [False] * 4,
            "value_exclusion_reason": [None] * 4,
        }
    )
    for horizon in (15, 30, 60, 90):
        frame[f"value_fwd_{horizon}_state_aligned_return_pips"] = [
            1.0,
            None,
            2.0,
            None,
        ]
    return frame


def test_synthetic_phase2_run_writes_report_contract(
    tmp_path: Path, monkeypatch
) -> None:
    config = load_project_config(
        PROJECT_ROOT / "config/research.yaml",
        PROJECT_ROOT / "config/sessions.yaml",
    )
    value_config = load_value_state_config(PROJECT_ROOT / "config/value_state.yaml")
    bars = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2024-01-05 00:00:00Z"]),
            "mid_activity_sum": [1.1],
            "mid_squared_activity_sum": [1.21],
        }
    )
    events = _synthetic_events()
    profiles = pd.DataFrame(
        {
            "profile_day": [date(2024, 1, 4)],
            "val": [1.0900],
            "poc": [1.0950],
            "vah": [1.1000],
            "eligible": [True],
            "m5_coverage_ratio": [1.0],
        }
    )
    comparisons = pd.DataFrame(
        {
            "contrast": ["outside_minus_inside_range"],
            "session_name": ["london"],
            "horizon_minutes": [60],
            "first_count": [30],
            "second_count": [30],
            "first_mean": [20.0],
            "second_mean": [17.0],
            "mean_difference": [3.0],
            "ci_low": [1.0],
            "ci_high": [5.0],
        }
    )

    monkeypatch.setattr(phase2, "load_m5_range", lambda *args: bars)
    monkeypatch.setattr(phase2, "enrich_fx_day_vwap", lambda *args, **kwargs: bars)
    monkeypatch.setattr(phase2, "build_session_calendar", lambda *args: pd.DataFrame())
    monkeypatch.setattr(phase2, "build_event_dataset", lambda *args, **kwargs: events)
    monkeypatch.setattr(phase2, "attach_event_vwap", lambda *args, **kwargs: events)
    monkeypatch.setattr(
        phase2, "build_daily_tick_profiles", lambda *args, **kwargs: profiles
    )
    monkeypatch.setattr(
        phase2, "attach_previous_profile", lambda *args, **kwargs: events
    )
    monkeypatch.setattr(phase2, "attach_value_outcomes", lambda *args, **kwargs: events)
    monkeypatch.setattr(
        phase2,
        "conditional_statistics",
        lambda *args, **kwargs: pd.DataFrame({"grouping": ["value_state"]}),
    )
    monkeypatch.setattr(
        phase2,
        "continuous_feature_associations",
        lambda *args: pd.DataFrame({"feature": ["vwap_distance_pips"]}),
    )
    monkeypatch.setattr(
        phase2, "value_state_comparisons", lambda *args, **kwargs: comparisons
    )
    monkeypatch.setattr(
        phase2, "_source_snapshot", lambda *args: [{"sha256": "synthetic"}]
    )

    def fake_figures(_events: pd.DataFrame, output: Path) -> list[Path]:
        output.mkdir(parents=True)
        paths = []
        for name in (
            "range_by_value_state.png",
            "continuation_by_horizon.png",
            "reentry_acceptance.png",
            "vwap_distance_vs_return.png",
        ):
            path = output / name
            path.write_bytes(b"synthetic")
            paths.append(path)
        return paths

    monkeypatch.setattr(phase2, "_create_figures", fake_figures)

    result = phase2.run_phase2(tmp_path, config, value_config)
    output = tmp_path / result["output_directory"]

    for relative in (
        "run_manifest.json",
        "data_quality.json",
        "daily_profiles.parquet",
        "value_events.parquet",
        "event_exclusions.parquet",
        "conditional_statistics.csv",
        "continuous_associations.csv",
        "statistical_comparisons.csv",
        "report.md",
    ):
        assert (output / relative).is_file()
    assert result["development_gate"]["checks"]["point_in_time_invariants"]
