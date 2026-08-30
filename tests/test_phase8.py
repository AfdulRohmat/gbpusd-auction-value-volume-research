from test_balance_boundary_strategy import (
    PIP_SIZE,
    _bars,
    _calendar,
    _config,
    _empty_transitions,
    _episodes,
    _sessions,
    _timeline,
)

from gbpusd_research.research.balance_boundary_strategy import (
    build_analysis_trades,
    simulate_balance_boundary,
)
from gbpusd_research.research.phase8 import event_funnel, execution_invariants


def test_rejection_run_passes_phase8_execution_invariants() -> None:
    bars = _bars()
    bars.loc[0, ["mid_low", "mid_close"]] = [1.1000, 1.1002]
    bars.loc[1, ["mid_open", "bid_open", "ask_open"]] = [
        1.10002,
        1.09997,
        1.10007,
    ]
    events, setup_trades = simulate_balance_boundary(
        _calendar(),
        bars,
        _timeline(bars),
        _episodes(),
        _empty_transitions(),
        _config(),
        _sessions(),
        pip_size=PIP_SIZE,
        sample_year=2025,
    )
    analysis_trades = build_analysis_trades(setup_trades, _config())
    result = execution_invariants(
        events,
        setup_trades,
        analysis_trades,
        _config(),
        pip_size=PIP_SIZE,
    )

    assert result["passed"], result["checks"]


def test_event_funnel_distinguishes_balance_context_and_trigger() -> None:
    bars = _bars()
    events, _ = simulate_balance_boundary(
        _calendar(),
        bars,
        _timeline(bars),
        _episodes(),
        _empty_transitions(),
        _config(),
        _sessions(),
        pip_size=PIP_SIZE,
        sample_year=2025,
    )
    funnel = event_funnel(events).set_index("stage")["count"]

    assert funnel["scheduled"] == 1
    assert funnel["observable_balance"] == 1
    assert funnel["valid_trigger"] == 0
    assert funnel["traded"] == 0
