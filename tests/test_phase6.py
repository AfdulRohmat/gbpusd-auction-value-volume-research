from pathlib import Path

import numpy as np
import pandas as pd

from gbpusd_research.config import load_opening_auction_config
from gbpusd_research.research.phase6 import (
    monthly_statistics,
    paired_exit_deltas,
    variant_statistics,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _config():
    config = load_opening_auction_config(
        PROJECT_ROOT / "config/opening_auction_state_machine.yaml"
    )
    return config.model_copy(
        update={
            "analysis": config.analysis.model_copy(
                update={"bootstrap_resamples": 100}
            )
        }
    )


def _trades() -> pd.DataFrame:
    rows = []
    outcomes = {"fixed_2r": 0.2, "session_hold": 0.1, "trailing_session": 0.3}
    for index in range(36):
        for variant, outcome in outcomes.items():
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
                    "r_multiple": outcome,
                    "pnl_pips": outcome * 10,
                    "mfe_r": 1.0,
                    "mae_r": 0.5,
                }
            )
    return pd.DataFrame(rows)


def test_variant_statistics_includes_combined_scope_and_expectancy() -> None:
    statistics = variant_statistics(_trades(), _config(), random_seed=1)
    combined = statistics[statistics["session_scope"].eq("combined")].set_index(
        "variant"
    )

    assert len(statistics) == 6
    assert combined.loc["fixed_2r", "trades"] == 36
    assert np.isclose(combined.loc["trailing_session", "expectancy_r"], 0.3)
    assert combined.loc["trailing_session", "benchmark_expectancy_met"]


def test_paired_exit_deltas_are_second_minus_first() -> None:
    deltas = paired_exit_deltas(_trades(), _config(), random_seed=1).set_index(
        "contrast"
    )

    assert np.isclose(
        deltas.loc["session_hold_minus_fixed_2r", "mean_delta_r"], -0.1
    )
    assert np.isclose(
        deltas.loc["trailing_minus_fixed_2r", "mean_delta_r"], 0.1
    )
    assert np.isclose(
        deltas.loc["trailing_minus_session_hold", "mean_delta_r"], 0.2
    )


def test_monthly_statistics_pool_both_sessions_per_variant() -> None:
    trades = _trades()
    london = trades.copy()
    london["session_name"] = "london"
    london["event_id"] = "london-" + london["event_id"]
    monthly = monthly_statistics(pd.concat([trades, london], ignore_index=True))

    fixed = monthly[
        monthly["variant"].eq("fixed_2r")
        & monthly["entry_month"].eq("2025-01")
    ].iloc[0]
    assert fixed["trades"] == 6
    assert fixed["positive_month"]
