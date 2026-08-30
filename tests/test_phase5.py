from pathlib import Path

import pandas as pd

from gbpusd_research.config import load_opening_ablation_config
from gbpusd_research.research.phase5 import paired_deltas, variant_statistics

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _config():
    return load_opening_ablation_config(
        PROJECT_ROOT / "config/opening_ablation.yaml"
    )


def _result_rows() -> pd.DataFrame:
    rows = []
    variants = {
        "signal_cohort_open_timeout_90": 2.0,
        "confirmed_timeout_all": 1.0,
        "confirmed_timeout_favorable": 1.0,
        "confirmed_poc_no_stop": 1.5,
        "phase4_full": 0.5,
    }
    for index in range(36):
        for variant, pnl in variants.items():
            rows.append(
                {
                    "sample_year": 2025,
                    "session_name": "new_york",
                    "variant": variant,
                    "event_id": f"event-{index}",
                    "local_session_date": pd.Timestamp(
                        2025, index % 12 + 1, 1
                    ).date(),
                    "entry_timestamp_utc": pd.Timestamp("2025-01-01", tz="UTC")
                    + pd.to_timedelta(index, unit="D"),
                    "direction": 1 if index % 2 else -1,
                    "pnl_pips": pnl,
                    "mfe_pips": 3.0,
                    "mae_pips": 1.0,
                }
            )
    return pd.DataFrame(rows)


def test_paired_deltas_use_common_events_and_second_minus_first() -> None:
    deltas = paired_deltas(_result_rows(), _config(), random_seed=20240801)
    indexed = deltas.set_index("contrast")

    assert indexed.loc["confirmation_delay", "common_events"] == 36
    assert indexed.loc["confirmation_delay", "mean_delta_pips"] == -1.0
    assert indexed.loc["poc_target", "mean_delta_pips"] == 0.5
    assert indexed.loc["excursion_stop", "mean_delta_pips"] == -1.0


def test_variant_statistics_marks_sufficient_sample() -> None:
    results = _result_rows()
    populations = pd.DataFrame(
        {
            "sample_year": [2025],
            "session_name": ["new_york"],
            "scheduled_events": [261],
            "outside_candidates": [165],
        }
    )
    statistics = variant_statistics(
        results,
        populations,
        _config(),
        random_seed=20240801,
    )

    assert len(statistics) == 5
    assert not statistics["underpowered"].any()
    assert (statistics["results"] == 36).all()
