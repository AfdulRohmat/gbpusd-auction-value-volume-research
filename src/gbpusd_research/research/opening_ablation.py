"""Executable component ablations for the Phase-4 opening-value rule."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from gbpusd_research.config import (
    OpeningAblationConfig,
    OpeningValueStrategyConfig,
)
from gbpusd_research.research.opening_value_strategy import (
    _complete_window,
    simulate_opening_value_strategy,
)


def _entry_price(
    bar: pd.Series, *, direction: int, slippage_price: float
) -> float:
    return (
        float(bar["ask_open"]) + slippage_price
        if direction > 0
        else float(bar["bid_open"]) - slippage_price
    )


def _timeout_exit(
    bar: pd.Series, *, direction: int, slippage_price: float
) -> float:
    return (
        float(bar["bid_close"]) - slippage_price
        if direction > 0
        else float(bar["ask_close"]) + slippage_price
    )


def _exit_without_stop(
    management: pd.DataFrame,
    *,
    direction: int,
    target_price: float | None,
    slippage_price: float,
) -> tuple[pd.Series, float, str]:
    if target_price is not None:
        for _, bar in management.iterrows():
            touched = (
                float(bar["bid_high"]) >= target_price
                if direction > 0
                else float(bar["ask_low"]) <= target_price
            )
            if touched:
                exit_price = (
                    target_price - slippage_price
                    if direction > 0
                    else target_price + slippage_price
                )
                return bar, exit_price, "target"
    bar = management.iloc[-1]
    return (
        bar,
        _timeout_exit(bar, direction=direction, slippage_price=slippage_price),
        "timeout",
    )


def _excursions(
    management: pd.DataFrame,
    *,
    direction: int,
    entry_price: float,
    pip_size: float,
) -> tuple[float, float]:
    if direction > 0:
        favorable = (float(management["bid_high"].max()) - entry_price) / pip_size
        adverse = (entry_price - float(management["bid_low"].min())) / pip_size
    else:
        favorable = (entry_price - float(management["ask_low"].min())) / pip_size
        adverse = (float(management["ask_high"].max()) - entry_price) / pip_size
    return max(0.0, favorable), max(0.0, adverse)


def _result_row(
    event: object,
    *,
    sample_year: int,
    variant: str,
    direction: int,
    entry_timestamp: pd.Timestamp,
    entry_price: float,
    exit_bar: pd.Series,
    exit_price: float,
    exit_reason: str,
    management: pd.DataFrame,
    pip_size: float,
    signal_timestamp: pd.Timestamp | None = None,
) -> dict[str, object]:
    pnl_pips = direction * (exit_price - entry_price) / pip_size
    mfe_pips, mae_pips = _excursions(
        management,
        direction=direction,
        entry_price=entry_price,
        pip_size=pip_size,
    )
    return {
        "sample_year": sample_year,
        "event_id": event.event_id,
        "session_name": event.session_name,
        "local_session_date": event.local_session_date,
        "event_timestamp_utc": event.event_timestamp_utc,
        "fx_trading_day": event.fx_trading_day,
        "previous_profile_day": event.previous_profile_day,
        "value_state": event.value_state,
        "direction": direction,
        "side": "long" if direction > 0 else "short",
        "variant": variant,
        "diagnostic_only": variant != "phase4_full",
        "signal_timestamp_utc": signal_timestamp,
        "entry_timestamp_utc": entry_timestamp,
        "entry_price": entry_price,
        "exit_bar_timestamp_utc": exit_bar["timestamp"],
        "exit_timestamp_utc": exit_bar["timestamp"] + timedelta(minutes=5),
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "pnl_pips": pnl_pips,
        "mfe_pips": mfe_pips,
        "mae_pips": mae_pips,
    }


def _append_open_variant(
    rows: list[dict[str, object]],
    event: object,
    window: pd.DataFrame,
    *,
    sample_year: int,
    variant: str,
    direction: int,
    horizon_minutes: int,
    target_price: float | None,
    slippage_price: float,
    pip_size: float,
) -> None:
    entry_bar = window.iloc[0]
    entry_price = _entry_price(
        entry_bar, direction=direction, slippage_price=slippage_price
    )
    management = window.iloc[: horizon_minutes // 5]
    exit_bar, exit_price, exit_reason = _exit_without_stop(
        management,
        direction=direction,
        target_price=target_price,
        slippage_price=slippage_price,
    )
    rows.append(
        _result_row(
            event,
            sample_year=sample_year,
            variant=variant,
            direction=direction,
            entry_timestamp=pd.Timestamp(entry_bar["timestamp"]),
            entry_price=entry_price,
            exit_bar=exit_bar,
            exit_price=exit_price,
            exit_reason=exit_reason,
            management=management.loc[: exit_bar.name],
            pip_size=pip_size,
        )
    )


def simulate_opening_ablation(
    events: pd.DataFrame,
    bars: pd.DataFrame,
    strategy_config: OpeningValueStrategyConfig,
    ablation_config: OpeningAblationConfig,
    *,
    pip_size: float,
    sample_year: int,
) -> pd.DataFrame:
    """Generate every preregistered Phase-5 variant without threshold search."""

    ordered = bars.copy()
    ordered["timestamp"] = pd.to_datetime(ordered["timestamp"], utc=True)
    ordered = ordered.sort_values("timestamp", kind="stable").reset_index(drop=True)
    execution = strategy_config.execution
    slippage_price = execution.slippage_per_side_pips * pip_size
    selected = set(ablation_config.analysis.variants)
    full = simulate_opening_value_strategy(
        events,
        ordered,
        strategy_config,
        pip_size=pip_size,
        sample_role=str(sample_year),
    ).set_index("event_id")
    rows: list[dict[str, object]] = []

    for event in events.itertuples(index=False):
        if not bool(event.value_eligible) or event.value_state not in {
            "above_value",
            "below_value",
        }:
            continue
        direction = -1 if event.value_state == "above_value" else 1
        opened = pd.Timestamp(event.event_timestamp_utc)
        window = _complete_window(
            ordered, opened, timeout_minutes=execution.timeout_minutes
        )
        if window is None:
            continue

        for horizon in (30, 60, 90):
            variant = f"open_timeout_{horizon}"
            if variant in selected:
                _append_open_variant(
                    rows,
                    event,
                    window,
                    sample_year=sample_year,
                    variant=variant,
                    direction=direction,
                    horizon_minutes=horizon,
                    target_price=None,
                    slippage_price=slippage_price,
                    pip_size=pip_size,
                )
        if "open_boundary_90" in selected:
            boundary = (
                float(event.previous_val)
                if direction > 0
                else float(event.previous_vah)
            )
            _append_open_variant(
                rows,
                event,
                window,
                sample_year=sample_year,
                variant="open_boundary_90",
                direction=direction,
                horizon_minutes=90,
                target_price=boundary,
                slippage_price=slippage_price,
                pip_size=pip_size,
            )
        if "open_poc_90" in selected:
            _append_open_variant(
                rows,
                event,
                window,
                sample_year=sample_year,
                variant="open_poc_90",
                direction=direction,
                horizon_minutes=90,
                target_price=float(event.previous_poc),
                slippage_price=slippage_price,
                pip_size=pip_size,
            )

        full_row = full.loc[event.event_id]
        if not bool(full_row["signal_found"]):
            continue
        signal_timestamp = pd.Timestamp(full_row["signal_timestamp_utc"])
        entry_timestamp = pd.Timestamp(full_row["entry_timestamp_utc"])
        entry_index = int(
            window.index[window["timestamp"].eq(entry_timestamp)][0]
        )
        entry_bar = window.loc[entry_index]
        confirmed_entry = _entry_price(
            entry_bar, direction=direction, slippage_price=slippage_price
        )
        confirmed_management = window.loc[entry_index:]

        if "signal_cohort_open_timeout_90" in selected:
            _append_open_variant(
                rows,
                event,
                window,
                sample_year=sample_year,
                variant="signal_cohort_open_timeout_90",
                direction=direction,
                horizon_minutes=90,
                target_price=None,
                slippage_price=slippage_price,
                pip_size=pip_size,
            )
        if "confirmed_timeout_all" in selected:
            exit_bar, exit_price, exit_reason = _exit_without_stop(
                confirmed_management,
                direction=direction,
                target_price=None,
                slippage_price=slippage_price,
            )
            rows.append(
                _result_row(
                    event,
                    sample_year=sample_year,
                    variant="confirmed_timeout_all",
                    direction=direction,
                    signal_timestamp=signal_timestamp,
                    entry_timestamp=entry_timestamp,
                    entry_price=confirmed_entry,
                    exit_bar=exit_bar,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    management=confirmed_management,
                    pip_size=pip_size,
                )
            )

        poc = float(event.previous_poc)
        favorable = poc > confirmed_entry if direction > 0 else poc < confirmed_entry
        if not favorable:
            continue
        if "confirmed_timeout_favorable" in selected:
            exit_bar, exit_price, exit_reason = _exit_without_stop(
                confirmed_management,
                direction=direction,
                target_price=None,
                slippage_price=slippage_price,
            )
            rows.append(
                _result_row(
                    event,
                    sample_year=sample_year,
                    variant="confirmed_timeout_favorable",
                    direction=direction,
                    signal_timestamp=signal_timestamp,
                    entry_timestamp=entry_timestamp,
                    entry_price=confirmed_entry,
                    exit_bar=exit_bar,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    management=confirmed_management,
                    pip_size=pip_size,
                )
            )
        if "confirmed_poc_no_stop" in selected:
            exit_bar, exit_price, exit_reason = _exit_without_stop(
                confirmed_management,
                direction=direction,
                target_price=poc,
                slippage_price=slippage_price,
            )
            rows.append(
                _result_row(
                    event,
                    sample_year=sample_year,
                    variant="confirmed_poc_no_stop",
                    direction=direction,
                    signal_timestamp=signal_timestamp,
                    entry_timestamp=entry_timestamp,
                    entry_price=confirmed_entry,
                    exit_bar=exit_bar,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    management=confirmed_management.loc[: exit_bar.name],
                    pip_size=pip_size,
                )
            )
        if "phase4_full" in selected and bool(full_row["trade_executed"]):
            exit_bar_index = int(
                window.index[
                    window["timestamp"].eq(full_row["exit_bar_timestamp_utc"])
                ][0]
            )
            exit_bar = window.loc[exit_bar_index]
            rows.append(
                _result_row(
                    event,
                    sample_year=sample_year,
                    variant="phase4_full",
                    direction=direction,
                    signal_timestamp=signal_timestamp,
                    entry_timestamp=entry_timestamp,
                    entry_price=float(full_row["entry_price"]),
                    exit_bar=exit_bar,
                    exit_price=float(full_row["exit_price"]),
                    exit_reason=str(full_row["exit_reason"]),
                    management=window.loc[entry_index:exit_bar_index],
                    pip_size=pip_size,
                )
            )
    return pd.DataFrame(rows)
