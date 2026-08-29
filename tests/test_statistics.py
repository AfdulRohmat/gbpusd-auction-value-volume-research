import pandas as pd

from gbpusd_research.research.statistics import paired_bootstrap_comparisons


def _events(kind: str, values: list[float]) -> pd.DataFrame:
    event_ids = [f"open-{index}" for index in range(len(values))]
    return pd.DataFrame(
        {
            "event_id": event_ids
            if kind == "session_open"
            else [f"c-{x}" for x in event_ids],
            "matched_event_id": [None] * len(values)
            if kind == "session_open"
            else event_ids,
            "event_kind": kind,
            "session_name": "london",
            "calendar_year": [2023, 2023, 2024, 2024],
            "eligible": True,
            "fwd_60_range_pips": values,
            "fwd_60_abs_return_pips": values,
            "fwd_60_range_over_pre60": values,
        }
    )


def test_paired_bootstrap_is_deterministic_and_reports_each_year() -> None:
    openings = _events("session_open", [10.0, 12.0, 14.0, 16.0])
    controls = _events("fixed_control", [5.0, 7.0, 8.0, 10.0])
    arguments = {
        "horizons": (60,),
        "resamples": 100,
        "confidence_level": 0.95,
        "random_seed": 7,
    }

    first = paired_bootstrap_comparisons(openings, controls, **arguments)
    second = paired_bootstrap_comparisons(openings, controls, **arguments)

    pd.testing.assert_frame_equal(first, second)
    assert set(first["analysis_scope"]) == {"all", "calendar_year"}
    yearly = first[
        first["analysis_scope"].eq("calendar_year") & first["metric"].eq("range_pips")
    ].set_index("calendar_year")
    assert yearly.loc[2023, "mean_difference"] == 5.0
    assert yearly.loc[2024, "mean_difference"] == 6.0
