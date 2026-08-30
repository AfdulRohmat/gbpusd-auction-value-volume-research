"""Executable Phase-8 strategies anchored to a frozen balance boundary."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from gbpusd_research.config import (
    BalanceBoundaryStrategyConfig,
    SessionsConfig,
)
from gbpusd_research.research.opening_auction_state_machine import (
    _entry_price,
    _exact_window,
    _excursions,
    _session_cutoff,
    _stop_fill,
    _timeout_fill,
)


def build_opening_balance_context(
    calendar: pd.DataFrame,
    timeline: pd.DataFrame,
    episodes: pd.DataFrame,
    *,
    pip_size: float,
    sample_year: int,
) -> pd.DataFrame:
    """Attach the last observable state and its point-in-time boundary."""

    events = calendar.copy().rename(
        columns={"open_timestamp_utc": "event_timestamp_utc"}
    )
    events["sample_year"] = sample_year
    events["event_id"] = (
        events["session_name"].astype(str)
        + "-"
        + events["local_session_date"].astype(str)
    )
    events = events.sort_values("event_timestamp_utc", kind="stable")
    state_columns = [
        "available_at",
        "raw_auction_state",
        "observable_state",
        "observable_episode_id",
        "activity_regime",
        "activity_ratio",
    ]
    state = timeline[state_columns].sort_values("available_at", kind="stable")
    merged = pd.merge_asof(
        events,
        state,
        left_on="event_timestamp_utc",
        right_on="available_at",
        direction="backward",
        tolerance=pd.Timedelta(5, unit="min"),
    )
    merged["state_staleness_minutes"] = (
        merged["event_timestamp_utc"] - merged["available_at"]
    ).dt.total_seconds() / 60
    for column in (
        "balance_episode_start_at",
        "balance_episode_confirmed_at",
        "boundary_available_at_max",
    ):
        merged[column] = pd.Series(
            pd.NaT,
            index=merged.index,
            dtype="datetime64[ns, UTC]",
        )
    for column in (
        "balance_age_minutes",
        "balance_high",
        "balance_low",
        "balance_midpoint",
        "balance_width_pips",
    ):
        merged[column] = np.nan
    merged["context_status"] = "eligible"
    merged["context_exclusion_reason"] = pd.Series(
        pd.NA, index=merged.index, dtype="string"
    )

    episode_lookup = episodes.set_index("episode_id", drop=False)
    for index, event in merged.iterrows():
        if pd.isna(event["available_at"]):
            merged.at[index, "context_status"] = "excluded"
            merged.at[index, "context_exclusion_reason"] = "state_unavailable"
            continue
        if event["observable_state"] != "balance" or pd.isna(
            event["observable_episode_id"]
        ):
            merged.at[index, "context_status"] = "excluded"
            merged.at[index, "context_exclusion_reason"] = "not_observable_balance"
            continue
        episode_id = str(event["observable_episode_id"])
        if episode_id not in episode_lookup.index:
            merged.at[index, "context_status"] = "excluded"
            merged.at[index, "context_exclusion_reason"] = "episode_not_found"
            continue
        episode = episode_lookup.loc[episode_id]
        available = pd.Timestamp(event["event_timestamp_utc"])
        start_at = pd.Timestamp(episode["start_at"])
        availability = pd.to_datetime(timeline["available_at"], utc=True)
        boundary_mask = availability.ge(start_at) & availability.le(available)
        if "segment_id" in timeline and "segment_id" in episode.index:
            boundary_mask &= timeline["segment_id"].eq(episode["segment_id"])
        boundary = timeline[boundary_mask]
        if boundary.empty:
            merged.at[index, "context_status"] = "excluded"
            merged.at[index, "context_exclusion_reason"] = "boundary_unavailable"
            continue
        high = float(boundary["mid_high"].max())
        low = float(boundary["mid_low"].min())
        if not np.isfinite(high) or not np.isfinite(low) or high <= low:
            merged.at[index, "context_status"] = "excluded"
            merged.at[index, "context_exclusion_reason"] = "invalid_boundary"
            continue
        merged.at[index, "balance_episode_start_at"] = start_at
        merged.at[index, "balance_episode_confirmed_at"] = pd.Timestamp(
            episode["confirmed_at"]
        )
        merged.at[index, "boundary_available_at_max"] = pd.Timestamp(
            boundary["available_at"].max()
        )
        merged.at[index, "balance_age_minutes"] = (
            available - start_at
        ).total_seconds() / 60
        merged.at[index, "balance_high"] = high
        merged.at[index, "balance_low"] = low
        merged.at[index, "balance_midpoint"] = (high + low) / 2
        merged.at[index, "balance_width_pips"] = (high - low) / pip_size
    return merged.reset_index(drop=True)


def _manage_trade(
    management: pd.DataFrame,
    *,
    variant: str,
    direction: int,
    entry_price: float,
    initial_stop: float,
    initial_risk_pips: float,
    target_fill: float | None,
    config: BalanceBoundaryStrategyConfig,
    pip_size: float,
) -> dict[str, object]:
    slippage_price = config.execution.slippage_per_side_pips * pip_size
    target_trigger = (
        target_fill + direction * slippage_price
        if target_fill is not None
        else np.nan
    )
    activation_trigger = entry_price + direction * (
        config.trailing.break_even_trigger_r * initial_risk_pips * pip_size
        + slippage_price
    )
    active_stop = initial_stop
    break_even_activated = False
    trailing_updates = 0
    exit_bar: pd.Series | None = None
    exit_price = np.nan
    exit_reason = "session_cutoff"
    ambiguous = False

    for position, (_, bar) in enumerate(management.iterrows()):
        stop_touched = (
            float(bar["bid_low"]) <= active_stop
            if direction > 0
            else float(bar["ask_high"]) >= active_stop
        )
        target_touched = target_fill is not None and (
            float(bar["bid_high"]) >= target_trigger
            if direction > 0
            else float(bar["ask_low"]) <= target_trigger
        )
        if stop_touched:
            exit_bar = bar
            exit_price = _stop_fill(
                bar,
                direction=direction,
                stop_trigger=active_stop,
                slippage_price=slippage_price,
            )
            exit_reason = "trailing_stop" if active_stop != initial_stop else "stop"
            ambiguous = bool(target_touched)
            break
        if target_touched:
            exit_bar = bar
            exit_price = float(target_fill)
            exit_reason = (
                "midpoint_target"
                if variant == "rotation_midpoint"
                else "target_2r"
            )
            break

        if variant == "acceptance_trailing_session":
            reached_activation = (
                float(bar["bid_high"]) >= activation_trigger
                if direction > 0
                else float(bar["ask_low"]) <= activation_trigger
            )
            if reached_activation:
                break_even_activated = True
            if break_even_activated:
                break_even_stop = entry_price + direction * slippage_price
                first = max(0, position - config.trailing.swing_bars + 1)
                completed = management.iloc[first : position + 1]
                if direction > 0:
                    swing_stop = (
                        float(completed["bid_low"].min())
                        - config.trailing.buffer_pips * pip_size
                    )
                    updated = max(active_stop, break_even_stop, swing_stop)
                else:
                    swing_stop = (
                        float(completed["ask_high"].max())
                        + config.trailing.buffer_pips * pip_size
                    )
                    updated = min(active_stop, break_even_stop, swing_stop)
                if updated != active_stop:
                    active_stop = updated
                    trailing_updates += 1

    if exit_bar is None:
        exit_bar = management.iloc[-1]
        exit_price = _timeout_fill(
            exit_bar,
            direction=direction,
            slippage_price=slippage_price,
        )

    exit_position = int(management.index.get_loc(exit_bar.name))
    observed = management.iloc[: exit_position + 1]
    pnl_pips = direction * (float(exit_price) - entry_price) / pip_size
    mfe_pips, mae_pips = _excursions(
        observed,
        direction=direction,
        entry_price=entry_price,
        pip_size=pip_size,
    )
    return {
        "target_trigger_price": target_trigger,
        "exit_bar_timestamp_utc": exit_bar["timestamp"],
        "exit_timestamp_utc": exit_bar["timestamp"] + timedelta(minutes=5),
        "exit_price": float(exit_price),
        "exit_reason": exit_reason,
        "final_stop_trigger_price": active_stop,
        "ambiguous_bar_stop_first": ambiguous,
        "break_even_activated": break_even_activated,
        "trailing_updates": trailing_updates,
        "pnl_pips": pnl_pips,
        "r_multiple": pnl_pips / initial_risk_pips,
        "mfe_pips": mfe_pips,
        "mae_pips": mae_pips,
        "mfe_r": mfe_pips / initial_risk_pips,
        "mae_r": mae_pips / initial_risk_pips,
    }


def _acceptance_direction(
    signal_bars: pd.DataFrame,
    position: int,
    state_row: pd.Series,
    transition_lookup: dict[tuple[str, str], pd.Series],
    *,
    opening_episode_id: str,
    high_threshold: float,
    low_threshold: float,
    consecutive_closes: int,
) -> int | None:
    if position + 1 < consecutive_closes:
        return None
    to_episode = state_row["observable_episode_id"]
    state = state_row["observable_state"]
    key = (opening_episode_id, str(to_episode))
    transition = transition_lookup.get(key)
    if transition is None or pd.Timestamp(transition["confirmed_at"]) != pd.Timestamp(
        state_row["available_at"]
    ):
        return None
    closes = signal_bars.iloc[
        position - consecutive_closes + 1 : position + 1
    ]["mid_close"]
    if state == "imbalance_up" and closes.ge(high_threshold).all():
        return 1
    if state == "imbalance_down" and closes.le(low_threshold).all():
        return -1
    return None


def _base_trade(
    row: dict[str, object],
    *,
    setup: str,
    variant: str,
    direction: int,
    signal_bar: pd.Series,
    state_row: pd.Series,
    entry_bar: pd.Series,
    entry_price: float,
    stop_trigger: float,
    nominal_stop_fill: float,
    initial_risk_pips: float,
    target_fill: float | None,
    nominal_reward_r: float | None,
) -> dict[str, object]:
    return {
        **row,
        "setup": setup,
        "variant": variant,
        "direction": direction,
        "side": "long" if direction > 0 else "short",
        "trigger_bar_timestamp_utc": signal_bar["timestamp"],
        "signal_available_at": state_row["available_at"],
        "signal_raw_state": state_row["raw_auction_state"],
        "signal_observable_state": state_row["observable_state"],
        "signal_observable_episode_id": state_row["observable_episode_id"],
        "trigger_high": float(signal_bar["mid_high"]),
        "trigger_low": float(signal_bar["mid_low"]),
        "trigger_close": float(signal_bar["mid_close"]),
        "entry_timestamp_utc": entry_bar["timestamp"],
        "entry_price": entry_price,
        "entry_bid_open": float(entry_bar["bid_open"]),
        "entry_ask_open": float(entry_bar["ask_open"]),
        "entry_spread_pips": row["entry_spread_pips"],
        "initial_stop_trigger_price": stop_trigger,
        "nominal_stop_fill_price": nominal_stop_fill,
        "initial_risk_pips": initial_risk_pips,
        "target_fill_price": target_fill,
        "nominal_reward_r": nominal_reward_r,
    }


def simulate_balance_boundary(
    calendar: pd.DataFrame,
    bars: pd.DataFrame,
    timeline: pd.DataFrame,
    episodes: pd.DataFrame,
    transitions: pd.DataFrame,
    config: BalanceBoundaryStrategyConfig,
    sessions: SessionsConfig,
    *,
    pip_size: float,
    sample_year: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Select the first point-in-time boundary setup and execute its exits."""

    ordered_bars = bars.copy()
    ordered_bars["timestamp"] = pd.to_datetime(ordered_bars["timestamp"], utc=True)
    ordered_bars = ordered_bars.sort_values("timestamp", kind="stable").reset_index(
        drop=True
    )
    timeline_by_timestamp = timeline.set_index("timestamp", drop=False)
    contexts = build_opening_balance_context(
        calendar,
        timeline,
        episodes,
        pip_size=pip_size,
        sample_year=sample_year,
    )
    primary = transitions[
        transitions["from_state"].eq("balance")
        & transitions["to_state"].isin(["imbalance_up", "imbalance_down"])
    ]
    transition_lookup = {
        (str(row.from_episode_id), str(row.to_episode_id)): pd.Series(row._asdict())
        for row in primary.itertuples(index=False)
    }
    slippage_price = config.execution.slippage_per_side_pips * pip_size
    stop_buffer_price = config.execution.stop_buffer_pips * pip_size
    touch = config.context.boundary_touch_tolerance_pips * pip_size
    rejection_inside = config.context.rejection_close_inside_pips * pip_size
    acceptance_outside = config.context.acceptance_close_outside_pips * pip_size
    signal_bars_count = config.context.signal_window_minutes // 5
    event_rows: list[dict[str, object]] = []
    trade_rows: list[dict[str, object]] = []

    for event in contexts.itertuples(index=False):
        row = event._asdict()
        row.update(
            cutoff_timestamp_utc=pd.NaT,
            strategy_status="excluded",
            strategy_exclusion_reason=row["context_exclusion_reason"],
            trigger_setup=None,
            trigger_direction=np.nan,
            trigger_bar_timestamp_utc=pd.NaT,
            signal_available_at=pd.NaT,
            entry_timestamp_utc=pd.NaT,
            entry_spread_pips=np.nan,
            invalid_rejection_signals=0,
            trade_executed=False,
        )
        if row["context_status"] != "eligible":
            event_rows.append(row)
            continue

        opened = pd.Timestamp(row["event_timestamp_utc"])
        cutoff = _session_cutoff(event, sessions)
        row["cutoff_timestamp_utc"] = cutoff
        window = _exact_window(ordered_bars, opened, cutoff)
        if window is None:
            row["strategy_exclusion_reason"] = "incomplete_session_window"
            event_rows.append(row)
            continue
        if len(window) <= signal_bars_count:
            row["strategy_exclusion_reason"] = "entry_bar_unavailable"
            event_rows.append(row)
            continue

        signal_bars = window.iloc[:signal_bars_count].reset_index(drop=True)
        high = float(row["balance_high"])
        low = float(row["balance_low"])
        midpoint = float(row["balance_midpoint"])
        high_acceptance = high + acceptance_outside
        low_acceptance = low - acceptance_outside
        opening_episode_id = str(row["observable_episode_id"])
        selected: dict[str, object] | None = None
        terminal_reason: str | None = None

        for position, signal_bar in signal_bars.iterrows():
            timestamp = pd.Timestamp(signal_bar["timestamp"])
            if timestamp not in timeline_by_timestamp.index:
                terminal_reason = "signal_state_unavailable"
                break
            state_row = timeline_by_timestamp.loc[timestamp]
            if isinstance(state_row, pd.DataFrame):
                state_row = state_row.iloc[-1]
            entry_bar = window.iloc[position + 1]
            direction = _acceptance_direction(
                signal_bars,
                position,
                state_row,
                transition_lookup,
                opening_episode_id=opening_episode_id,
                high_threshold=high_acceptance,
                low_threshold=low_acceptance,
                consecutive_closes=config.context.acceptance_consecutive_closes,
            )
            if direction is not None:
                stop_trigger = (
                    high - stop_buffer_price
                    if direction > 0
                    else low + stop_buffer_price
                )
                entry_price = _entry_price(
                    entry_bar,
                    direction=direction,
                    slippage_price=slippage_price,
                )
                nominal_stop_fill = (
                    stop_trigger - slippage_price
                    if direction > 0
                    else stop_trigger + slippage_price
                )
                risk = direction * (entry_price - nominal_stop_fill) / pip_size
                if not np.isfinite(risk) or risk <= 0:
                    terminal_reason = "accepted_break_nonpositive_risk"
                    break
                row["entry_spread_pips"] = (
                    float(entry_bar["ask_open"]) - float(entry_bar["bid_open"])
                ) / pip_size
                selected = {
                    "setup": "acceptance",
                    "direction": direction,
                    "signal_bar": signal_bar,
                    "state_row": state_row,
                    "entry_bar": entry_bar,
                    "entry_position": position + 1,
                    "entry_price": entry_price,
                    "stop_trigger": stop_trigger,
                    "risk": risk,
                    "nominal_stop_fill": nominal_stop_fill,
                    "acceptance_prior_close": float(
                        signal_bars.iloc[
                            position - config.context.acceptance_consecutive_closes + 1
                        ]["mid_close"]
                    ),
                    "acceptance_closes_outside": (
                        config.context.acceptance_consecutive_closes
                    ),
                }
                break

            same_episode = (
                state_row["observable_state"] == "balance"
                and str(state_row["observable_episode_id"]) == opening_episode_id
            )
            raw_allowed = str(state_row["raw_auction_state"]) in set(
                config.context.rejection_raw_states
            )
            upper = (
                float(signal_bar["mid_high"]) >= high - touch
                and float(signal_bar["mid_close"]) <= high - rejection_inside
            )
            lower = (
                float(signal_bar["mid_low"]) <= low + touch
                and float(signal_bar["mid_close"]) >= low + rejection_inside
            )
            if not same_episode or not raw_allowed or not (upper or lower):
                continue
            if upper and lower:
                terminal_reason = "ambiguous_dual_boundary_rejection"
                break
            direction = -1 if upper else 1
            stop_trigger = (
                high + stop_buffer_price if upper else low - stop_buffer_price
            )
            entry_price = _entry_price(
                entry_bar,
                direction=direction,
                slippage_price=slippage_price,
            )
            nominal_stop_fill = (
                stop_trigger - slippage_price
                if direction > 0
                else stop_trigger + slippage_price
            )
            risk = direction * (entry_price - nominal_stop_fill) / pip_size
            reward = direction * (midpoint - entry_price) / pip_size
            reward_r = reward / risk if risk > 0 else np.nan
            if (
                not np.isfinite(risk)
                or risk <= 0
                or not np.isfinite(reward_r)
                or reward_r < config.execution.minimum_rotation_reward_to_risk
            ):
                row["invalid_rejection_signals"] += 1
                continue
            row["entry_spread_pips"] = (
                float(entry_bar["ask_open"]) - float(entry_bar["bid_open"])
            ) / pip_size
            selected = {
                "setup": "rejection",
                "direction": direction,
                "signal_bar": signal_bar,
                "state_row": state_row,
                "entry_bar": entry_bar,
                "entry_position": position + 1,
                "entry_price": entry_price,
                "stop_trigger": stop_trigger,
                "risk": risk,
                "nominal_stop_fill": nominal_stop_fill,
                "reward_r": reward_r,
                "acceptance_prior_close": np.nan,
                "acceptance_closes_outside": 0,
            }
            break

        if selected is None:
            row["strategy_status"] = "no_trade"
            row["strategy_exclusion_reason"] = (
                terminal_reason or "no_valid_boundary_trigger"
            )
            event_rows.append(row)
            continue

        setup = str(selected["setup"])
        direction = int(selected["direction"])
        signal_bar = selected["signal_bar"]
        state_row = selected["state_row"]
        entry_bar = selected["entry_bar"]
        entry_price = float(selected["entry_price"])
        stop_trigger = float(selected["stop_trigger"])
        risk = float(selected["risk"])
        row.update(
            strategy_status="traded",
            strategy_exclusion_reason=None,
            trigger_setup=setup,
            trigger_direction=direction,
            trigger_bar_timestamp_utc=signal_bar["timestamp"],
            signal_available_at=state_row["available_at"],
            entry_timestamp_utc=entry_bar["timestamp"],
            trade_executed=True,
        )
        event_rows.append(row)
        management = window.iloc[int(selected["entry_position"]) :].reset_index(
            drop=True
        )
        variants = (
            ("rotation_midpoint",)
            if setup == "rejection"
            else ("acceptance_fixed_2r", "acceptance_trailing_session")
        )
        for variant in variants:
            if variant == "rotation_midpoint":
                target_fill = midpoint
                reward_r = float(selected["reward_r"])
            elif variant == "acceptance_fixed_2r":
                target_fill = entry_price + direction * (
                    config.execution.breakout_target_r_multiple * risk * pip_size
                )
                reward_r = config.execution.breakout_target_r_multiple
            else:
                target_fill = None
                reward_r = None
            trade = _base_trade(
                row,
                setup=setup,
                variant=variant,
                direction=direction,
                signal_bar=signal_bar,
                state_row=state_row,
                entry_bar=entry_bar,
                entry_price=entry_price,
                stop_trigger=stop_trigger,
                nominal_stop_fill=float(selected["nominal_stop_fill"]),
                initial_risk_pips=risk,
                target_fill=target_fill,
                nominal_reward_r=reward_r,
            )
            managed = _manage_trade(
                management,
                variant=variant,
                direction=direction,
                entry_price=entry_price,
                initial_stop=stop_trigger,
                initial_risk_pips=risk,
                target_fill=target_fill,
                config=config,
                pip_size=pip_size,
            )
            trade.update(
                **managed,
                acceptance_transition_from_episode_id=(
                    opening_episode_id if setup == "acceptance" else None
                ),
                acceptance_transition_to_episode_id=(
                    state_row["observable_episode_id"]
                    if setup == "acceptance"
                    else None
                ),
                acceptance_prior_close=selected["acceptance_prior_close"],
                acceptance_closes_outside=selected["acceptance_closes_outside"],
                holding_minutes=(
                    pd.Timestamp(managed["exit_timestamp_utc"])
                    - pd.Timestamp(entry_bar["timestamp"])
                ).total_seconds()
                / 60,
            )
            trade_rows.append(trade)

    return pd.DataFrame(event_rows), pd.DataFrame(trade_rows)


def build_analysis_trades(
    setup_trades: pd.DataFrame,
    config: BalanceBoundaryStrategyConfig,
) -> pd.DataFrame:
    """Add the two registered combined routes without changing executions."""

    frames = [setup_trades.assign(analysis_variant=setup_trades["variant"])]
    rotation = setup_trades[setup_trades["variant"].eq("rotation_midpoint")]
    fixed = setup_trades[setup_trades["variant"].eq("acceptance_fixed_2r")]
    trailing = setup_trades[
        setup_trades["variant"].eq("acceptance_trailing_session")
    ]
    frames.append(
        pd.concat([rotation, fixed], ignore_index=True).assign(
            analysis_variant="combined_fixed_2r"
        )
    )
    frames.append(
        pd.concat([rotation, trailing], ignore_index=True).assign(
            analysis_variant="combined_trailing_session"
        )
    )
    result = pd.concat(frames, ignore_index=True)
    registered = set(config.analysis.setup_variants) | set(
        config.analysis.portfolio_variants
    )
    if not set(result["analysis_variant"]).issubset(registered):
        raise ValueError("Unregistered Phase-8 analysis variant")
    return result
