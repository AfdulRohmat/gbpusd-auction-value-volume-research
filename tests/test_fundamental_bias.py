import pandas as pd

from gbpusd_research.research.fundamental_bias import (
    attach_fundamental_outcomes,
    fundamental_comparisons,
)


def _events() -> pd.DataFrame:
    rows = []
    for index in range(40):
        supports = index < 20
        bias = -1 if supports else 1
        rows.append(
            {
                "session_name": "new_york",
                "value_state": "above_value",
                "fundamental_bias": bias,
                "fundamental_bias_label": "short" if bias < 0 else "long",
                "fundamental_available": True,
                "value_eligible": True,
                "fwd_60_return_pips": -5.0 if supports else 1.0,
                "fwd_60_range_pips": 10.0,
                "fwd_60_up_excursion_pips": 3.0,
                "fwd_60_down_excursion_pips": 7.0,
            }
        )
    return pd.DataFrame(rows)


def test_bias_and_value_reversion_outcomes_have_correct_direction() -> None:
    result = attach_fundamental_outcomes(_events(), (60,))

    supports = result.iloc[0]
    opposes = result.iloc[-1]
    assert supports["policy_value_relation"] == "supports_reversion"
    assert supports["fundamental_fwd_60_bias_aligned_return_pips"] == 5.0
    assert supports["fundamental_fwd_60_reversion_aligned_return_pips"] == 5.0
    assert supports["fundamental_fwd_60_bias_aligned_mfe_pips"] == 7.0
    assert opposes["policy_value_relation"] == "opposes_reversion"
    assert opposes["fundamental_fwd_60_bias_aligned_return_pips"] == 1.0


def test_fundamental_bootstrap_is_deterministic() -> None:
    events = attach_fundamental_outcomes(_events(), (60,))
    arguments = {
        "horizons": (60,),
        "resamples": 200,
        "confidence_level": 0.95,
        "random_seed": 42,
    }

    first = fundamental_comparisons(events, **arguments)
    second = fundamental_comparisons(events, **arguments)

    pd.testing.assert_frame_equal(first, second)
    interaction = first[
        first["contrast"].eq("supports_minus_opposes_value_reversion")
    ].iloc[0]
    assert interaction["mean_difference"] == 6.0
