import pandas as pd

from gbpusd_research.research.phase3c import (
    _cluster_bootstrap_mean,
    attach_session_day_outcomes,
    directional_statistics,
)


def test_session_day_outcomes_use_future_open_of_same_session() -> None:
    rows = []
    for day in range(1, 7):
        for session, hour, offset in (("london", 8, 0.0), ("new_york", 13, 0.005)):
            rows.append(
                {
                    "event_timestamp_utc": pd.Timestamp(
                        f"2024-01-{day:02d} {hour:02d}:00:00Z"
                    ),
                    "session_name": session,
                    "open_price_mid": 1.25 + day * 0.001 + offset,
                    "value_state": "inside_value",
                    "value_eligible": True,
                    "repricing_available": True,
                    "repricing_unavailable_reason": None,
                    "repricing_bias": 1,
                }
            )
    events = pd.DataFrame(rows)

    result = attach_session_day_outcomes(
        events, horizons=(1, 3, 5), pip_size=0.0001
    )
    first_london = result[result["session_name"].eq("london")].iloc[0]

    assert round(first_london["repricing_fwd_1d_return_pips"], 8) == 10.0
    assert round(first_london["repricing_fwd_3d_return_pips"], 8) == 30.0
    assert round(first_london["repricing_fwd_5d_return_pips"], 8) == 50.0
    assert first_london["repricing_fwd_1d_end_timestamp_utc"] == pd.Timestamp(
        "2024-01-02 08:00:00Z"
    )


def test_cluster_bootstrap_is_deterministic() -> None:
    frame = pd.DataFrame(
        {
            "regime": ["a", "a", "b", "b", "c", "c"],
            "value": [1.0, 2.0, -1.0, 0.0, 3.0, 4.0],
        }
    )
    arguments = {
        "value_column": "value",
        "cluster_column": "regime",
        "resamples": 200,
        "confidence_level": 0.975,
        "random_seed": 42,
    }

    assert _cluster_bootstrap_mean(frame, **arguments) == _cluster_bootstrap_mean(
        frame, **arguments
    )


def test_directional_statistics_count_unique_regimes_not_rows() -> None:
    events = pd.DataFrame(
        {
            "session_name": ["london"] * 6,
            "repricing_eligible": [True] * 6,
            "repricing_bias": [1, 1, 1, -1, -1, -1],
            "repricing_bias_label": ["long"] * 3 + ["short"] * 3,
            "repricing_regime_id": ["a", "a", "b", "c", "c", "d"],
            "repricing_fwd_1d_aligned_return_pips": [1, 2, 3, 4, 5, 6],
        }
    )

    result = directional_statistics(
        events,
        horizons=(1,),
        resamples=200,
        confidence_level=0.975,
        random_seed=42,
    ).iloc[0]

    assert result["count"] == 6
    assert result["unique_regimes"] == 4
    assert result["long_regimes"] == 2
    assert result["short_regimes"] == 2
