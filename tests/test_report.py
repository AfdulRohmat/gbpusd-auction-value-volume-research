from pathlib import Path

import pandas as pd

from gbpusd_research.research.report import create_figures, render_markdown


def test_report_contract_generates_four_figures_and_gate_status(tmp_path: Path) -> None:
    rows = []
    opens = {
        "london": pd.Timestamp("2024-01-02 08:00Z"),
        "new_york": pd.Timestamp("2024-01-02 13:00Z"),
    }
    for session, opened in opens.items():
        for kind, value in (
            ("session_open", 20.0),
            ("fixed_control", 10.0),
            ("matched_control", 12.0),
        ):
            row = {
                "event_kind": kind,
                "session_name": session,
                "event_timestamp_utc": opened,
                "calendar_year": 2024,
                "eligible": True,
                "fwd_60_range_over_pre60": value / 10,
            }
            row.update(
                {f"fwd_{horizon}_range_pips": value for horizon in (5, 15, 30, 60, 90)}
            )
            rows.append(row)
    events = pd.DataFrame(rows)
    bars = pd.concat(
        [
            pd.DataFrame(
                {
                    "timestamp": pd.date_range(
                        opened - pd.Timedelta(30, unit="min"),
                        opened + pd.Timedelta(90, unit="min"),
                        freq="5min",
                        inclusive="left",
                    ),
                    "spread_median_pips": 0.8,
                }
            )
            for opened in opens.values()
        ],
        ignore_index=True,
    )

    paths = create_figures(events, bars, tmp_path / "figures")

    assert {path.name for path in paths} == {
        "distribution_by_session.png",
        "normalized_range_by_year.png",
        "quantiles_by_horizon.png",
        "spread_around_open.png",
    }
    assert all(path.is_file() for path in paths)

    openings = events[events["event_kind"].eq("session_open")]
    controls = events[~events["event_kind"].eq("session_open")]
    comparisons = pd.DataFrame(
        [
            {
                "analysis_scope": scope,
                "calendar_year": year,
                "control_kind": "fixed_control",
                "session_name": "london",
                "horizon_minutes": 60,
                "metric": "range_pips",
                "pair_count": 1,
                "opening_mean": 20.0,
                "control_mean": 10.0,
                "mean_difference": 10.0,
                "mean_ci_low": 9.0,
                "mean_ci_high": 11.0,
                "probability_opening_exceeds_control": 1.0,
            }
            for scope, year in (("all", None), ("calendar_year", 2024))
        ]
    )
    quality = {
        "opening_coverage_by_session_year": [
            {
                "calendar_year": 2024,
                "session_name": "london",
                "scheduled": 1,
                "eligible": 0,
                "eligible_ratio": 0.0,
            }
        ],
        "research_gate": {
            "passed": False,
            "development_passed": False,
            "checks": {"opening_coverage_by_session_year": False},
        },
    }
    markdown = render_markdown(
        openings,
        controls,
        comparisons,
        data_quality=quality,
        start="2024-01-01",
        end="2024-02-01",
    )
    assert "Registered Phase-1 gate: **FAIL**" in markdown
    assert "should not start" in markdown

    quality["research_gate"]["development_passed"] = True
    development_markdown = render_markdown(
        openings,
        controls,
        comparisons,
        data_quality=quality,
        start="2024-01-01",
        end="2025-01-01",
    )
    assert "DEVELOPMENT PASS / VALIDATION PENDING" in development_markdown
    assert "exploratory development may proceed" in development_markdown
