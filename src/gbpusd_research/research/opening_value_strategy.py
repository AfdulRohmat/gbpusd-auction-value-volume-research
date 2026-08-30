"""Executable opening-only previous-value reversion strategy."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from gbpusd_research.config import OpeningValueStrategyConfig


def _empty_result(event: object, *, sample_role: str) -> dict[str, object]:
    return {
        "sample_role": sample_role,
        "event_id": event.event_id,
        "session_name": event.session_name,
        "local_session_date": event.local_session_date,
        "event_timestamp_utc": event.event_timestamp_utc,
        "fx_trading_day": event.fx_trading_day,
        "value_state": getattr(event, "value_state", None),
        "previous_profile_day": getattr(event, "previous_profile_day", None),
        "previous_poc": getattr(event, "previous_poc", np.nan),
        "previous_vah": getattr(event, "previous_vah", np.nan),
        "previous_val": getattr(event, "previous_val", np.nan),
        "phase1_eligible": bool(getattr(event, "eligible", False)),
        "value_eligible": bool(getattr(event, "value_eligible", False)),
        "strategy_status": None,
        "strategy_exclusion_reason": None,
        "candidate": False,
        "direction": np.nan,
        "side": None,
        "signal_found": False,
        "signal_timestamp_utc": pd.NaT,
        "entry_timestamp_utc": pd.NaT,
        "entry_price": np.nan,
        "entry_bid_open": np.nan,
        "entry_ask_open": np.nan,
        "entry_spread_pips": np.nan,
        "known_excursion_price": np.nan,
        "stop_price": np.nan,
        "target_price": np.nan,
        "initial_risk_pips": np.nan,
        "target_reward_pips": np.nan,
        "reward_to_risk": np.nan,
        "trade_executed": False,
        "exit_bar_timestamp_utc": pd.NaT,
        "exit_timestamp_utc": pd.NaT,
        "exit_price": np.nan,
        "exit_reason": None,
        "pnl_pips": np.nan,
        "ambiguous_bar_stop_first": False,
        "holding_minutes": np.nan,
        "r_multiple": np.nan,
        "stressed_pnl_pips": np.nan,
        "stressed_r_multiple": np.nan,
    }


def _complete_window(
    bars: pd.DataFrame,
    opened: pd.Timestamp,
    *,
    timeout_minutes: int,
) -> pd.DataFrame | None:
    expected = pd.date_range(
        opened,
        opened + timedelta(minutes=timeout_minutes - 5),
        freq="5min",
    )
    frame = bars[
        bars["timestamp"].ge(opened)
        & bars["timestamp"].lt(opened + timedelta(minutes=timeout_minutes))
    ].copy()
    if len(frame) != len(expected) or not frame["timestamp"].reset_index(
        drop=True
    ).equals(pd.Series(expected)):
        return None
    return frame.reset_index(drop=True)


def _exit_trade(
    management: pd.DataFrame,
    *,
    direction: int,
    entry_price: float,
    stop_price: float,
    target_price: float,
    slippage_price: float,
    pip_size: float,
) -> dict[str, object]:
    for bar in management.itertuples(index=False):
        if direction > 0:
            stop_touched = bar.bid_low <= stop_price
            target_touched = bar.bid_high >= target_price
        else:
            stop_touched = bar.ask_high >= stop_price
            target_touched = bar.ask_low <= target_price
        if stop_touched:
            if direction > 0:
                raw_exit = min(stop_price, float(bar.bid_open))
                exit_price = raw_exit - slippage_price
            else:
                raw_exit = max(stop_price, float(bar.ask_open))
                exit_price = raw_exit + slippage_price
            exit_reason = "stop"
        elif target_touched:
            exit_price = (
                target_price - slippage_price
                if direction > 0
                else target_price + slippage_price
            )
            exit_reason = "target"
        else:
            continue
        pnl_pips = (
            direction * (exit_price - entry_price) / pip_size
        )
        return {
            "exit_bar_timestamp_utc": bar.timestamp,
            "exit_timestamp_utc": bar.timestamp + timedelta(minutes=5),
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "pnl_pips": pnl_pips,
            "ambiguous_bar_stop_first": bool(stop_touched and target_touched),
        }

    final_bar = management.iloc[-1]
    exit_price = (
        float(final_bar["bid_close"]) - slippage_price
        if direction > 0
        else float(final_bar["ask_close"]) + slippage_price
    )
    pnl_pips = direction * (exit_price - entry_price) / pip_size
    return {
        "exit_bar_timestamp_utc": final_bar["timestamp"],
        "exit_timestamp_utc": final_bar["timestamp"] + timedelta(minutes=5),
        "exit_price": exit_price,
        "exit_reason": "timeout",
        "pnl_pips": pnl_pips,
        "ambiguous_bar_stop_first": False,
    }


def simulate_opening_value_strategy(
    events: pd.DataFrame,
    bars: pd.DataFrame,
    config: OpeningValueStrategyConfig,
    *,
    pip_size: float,
    sample_role: str,
) -> pd.DataFrame:
    """Simulate one frozen opening-value candidate per eligible session event."""

    ordered = bars.copy()
    ordered["timestamp"] = pd.to_datetime(ordered["timestamp"], utc=True)
    ordered = ordered.sort_values("timestamp", kind="stable").reset_index(drop=True)
    execution = config.execution
    slippage_price = execution.slippage_per_side_pips * pip_size
    stress_increment_pips = 2 * (
        execution.stress_slippage_per_side_pips
        - execution.slippage_per_side_pips
    )
    rows = []
    for event in events.itertuples(index=False):
        row = _empty_result(event, sample_role=sample_role)
        if not bool(getattr(event, "value_eligible", False)):
            row.update(
                strategy_status="excluded",
                strategy_exclusion_reason="phase2_value_ineligible",
            )
            rows.append(row)
            continue
        state = event.value_state
        if state not in {"above_value", "below_value"}:
            row.update(
                strategy_status="no_candidate",
                strategy_exclusion_reason="inside_value",
            )
            rows.append(row)
            continue
        direction = -1 if state == "above_value" else 1
        side = "short" if direction < 0 else "long"
        row.update(candidate=True, direction=direction, side=side)
        opened = pd.Timestamp(event.event_timestamp_utc)
        window = _complete_window(
            ordered,
            opened,
            timeout_minutes=execution.timeout_minutes,
        )
        if window is None:
            row.update(
                strategy_status="excluded",
                strategy_exclusion_reason="incomplete_opening_window",
            )
            rows.append(row)
            continue
        signal_limit = opened + timedelta(
            minutes=execution.entry_deadline_minutes
        )
        signal_bars = window[window["timestamp"].lt(signal_limit)]
        condition = (
            signal_bars["mid_close"].le(event.previous_vah)
            if direction < 0
            else signal_bars["mid_close"].ge(event.previous_val)
        )
        if not condition.any():
            row.update(
                strategy_status="no_signal",
                strategy_exclusion_reason="no_completed_reentry_by_deadline",
            )
            rows.append(row)
            continue
        signal_index = condition[condition].index[0]
        signal_bar = window.loc[signal_index]
        signal_timestamp = pd.Timestamp(signal_bar["timestamp"])
        entry_timestamp = signal_timestamp + timedelta(minutes=5)
        entry_rows = window[window["timestamp"].eq(entry_timestamp)]
        if entry_rows.empty or entry_timestamp > signal_limit:
            row.update(
                strategy_status="excluded",
                strategy_exclusion_reason="missing_or_late_entry_bar",
            )
            rows.append(row)
            continue
        entry_bar = entry_rows.iloc[0]
        entry_price = (
            float(entry_bar["ask_open"]) + slippage_price
            if direction > 0
            else float(entry_bar["bid_open"]) - slippage_price
        )
        known = window[window["timestamp"].le(signal_timestamp)]
        stop_price = (
            float(known["mid_low"].min())
            - execution.stop_buffer_pips * pip_size
            if direction > 0
            else float(known["mid_high"].max())
            + execution.stop_buffer_pips * pip_size
        )
        target_price = float(event.previous_poc)
        target_favorable = (
            target_price > entry_price if direction > 0 else target_price < entry_price
        )
        if not target_favorable:
            row.update(
                signal_found=True,
                signal_timestamp_utc=signal_timestamp,
                entry_timestamp_utc=entry_timestamp,
                strategy_status="excluded",
                strategy_exclusion_reason="poc_target_not_favorable",
            )
            rows.append(row)
            continue
        stop_exit_price = (
            stop_price - slippage_price
            if direction > 0
            else stop_price + slippage_price
        )
        initial_risk_pips = (
            direction * (entry_price - stop_exit_price) / pip_size
        )
        if initial_risk_pips <= 0:
            row.update(
                signal_found=True,
                signal_timestamp_utc=signal_timestamp,
                entry_timestamp_utc=entry_timestamp,
                strategy_status="excluded",
                strategy_exclusion_reason="nonpositive_initial_risk",
            )
            rows.append(row)
            continue
        target_exit_price = (
            target_price - slippage_price
            if direction > 0
            else target_price + slippage_price
        )
        reward_pips = direction * (target_exit_price - entry_price) / pip_size
        management = window[window["timestamp"].ge(entry_timestamp)]
        exit_result = _exit_trade(
            management,
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            slippage_price=slippage_price,
            pip_size=pip_size,
        )
        pnl_pips = float(exit_result["pnl_pips"])
        stressed_pnl_pips = pnl_pips - stress_increment_pips
        row.update(
            signal_found=True,
            trade_executed=True,
            strategy_status="traded",
            strategy_exclusion_reason=None,
            signal_timestamp_utc=signal_timestamp,
            entry_timestamp_utc=entry_timestamp,
            entry_price=entry_price,
            entry_bid_open=float(entry_bar["bid_open"]),
            entry_ask_open=float(entry_bar["ask_open"]),
            entry_spread_pips=(
                float(entry_bar["ask_open"]) - float(entry_bar["bid_open"])
            )
            / pip_size,
            stop_price=stop_price,
            known_excursion_price=(
                float(known["mid_low"].min())
                if direction > 0
                else float(known["mid_high"].max())
            ),
            target_price=target_price,
            initial_risk_pips=initial_risk_pips,
            target_reward_pips=reward_pips,
            reward_to_risk=reward_pips / initial_risk_pips,
            **exit_result,
            holding_minutes=(
                pd.Timestamp(exit_result["exit_timestamp_utc"]) - entry_timestamp
            ).total_seconds()
            / 60,
            r_multiple=pnl_pips / initial_risk_pips,
            stressed_pnl_pips=stressed_pnl_pips,
            stressed_r_multiple=stressed_pnl_pips / initial_risk_pips,
        )
        rows.append(row)
    return pd.DataFrame(rows)
