"""Price-only opening-auction state classification and executable simulation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from gbpusd_research.config import OpeningAuctionConfig, SessionsConfig


def _session_cutoff(event: object, sessions: SessionsConfig) -> pd.Timestamp:
    local_date = pd.Timestamp(event.local_session_date).date()
    if event.session_name == "london":
        new_york = sessions.sessions["new_york"]
        value = datetime.combine(
            local_date,
            new_york.open,
            tzinfo=ZoneInfo(new_york.timezone),
        )
    elif event.session_name == "new_york":
        trading_day = sessions.trading_day
        value = datetime.combine(
            local_date,
            trading_day.boundary,
            tzinfo=ZoneInfo(trading_day.timezone),
        )
    else:
        raise ValueError(f"Unsupported opening-auction session: {event.session_name}")
    return pd.Timestamp(value.astimezone(UTC))


def _exact_window(
    bars: pd.DataFrame,
    opened: pd.Timestamp,
    cutoff: pd.Timestamp,
) -> pd.DataFrame | None:
    expected = pd.date_range(opened, cutoff - timedelta(minutes=5), freq="5min")
    frame = bars[
        bars["timestamp"].ge(opened) & bars["timestamp"].lt(cutoff)
    ].copy()
    observed = frame["timestamp"].reset_index(drop=True)
    if len(frame) != len(expected) or not observed.equals(pd.Series(expected)):
        return None
    return frame.reset_index(drop=True)


def classify_opening_bars(
    observation: pd.DataFrame,
    config: OpeningAuctionConfig,
) -> dict[str, float | int | str | None]:
    """Classify completed opening bars without using the future entry bar."""

    expected_bars = config.classification.observation_minutes // 5
    if len(observation) != expected_bars:
        raise ValueError(f"Opening classification requires {expected_bars} M5 bars")
    opened = float(observation.iloc[0]["mid_open"])
    high = float(observation["mid_high"].max())
    low = float(observation["mid_low"].min())
    close = float(observation.iloc[-1]["mid_close"])
    midpoint = (high + low) / 2
    closes = np.concatenate(([opened], observation["mid_close"].to_numpy(float)))
    path = float(np.abs(np.diff(closes)).sum())
    displacement = close - opened
    efficiency = abs(displacement) / path if path > 0 else 0.0
    opening_range = high - low
    close_location = (close - low) / opening_range if opening_range > 0 else 0.5
    threshold = config.classification.imbalance_efficiency_threshold
    extreme = config.classification.extreme_close_fraction

    if displacement > 0 and efficiency >= threshold and close_location >= extreme:
        state, direction = "imbalance_up", 1
    elif (
        displacement < 0
        and efficiency >= threshold
        and close_location <= 1 - extreme
    ):
        state, direction = "imbalance_down", -1
    elif close > midpoint:
        state, direction = "balance_high", -1
    elif close < midpoint:
        state, direction = "balance_low", 1
    elif displacement > 0:
        state, direction = "balance_high", -1
    elif displacement < 0:
        state, direction = "balance_low", 1
    else:
        state, direction = "no_direction", None

    return {
        "auction_state": state,
        "direction": direction,
        "opening_mid": opened,
        "opening_range_high": high,
        "opening_range_low": low,
        "opening_range_midpoint": midpoint,
        "opening_range_price": opening_range,
        "opening_close": close,
        "displacement_price": displacement,
        "path_price": path,
        "efficiency": efficiency,
        "close_location": close_location,
    }


def _entry_price(
    bar: pd.Series,
    *,
    direction: int,
    slippage_price: float,
) -> float:
    if direction > 0:
        return float(bar["ask_open"]) + slippage_price
    return float(bar["bid_open"]) - slippage_price


def _initial_stop(
    state: str,
    features: dict[str, float | int | str | None],
    *,
    buffer_price: float,
) -> float:
    if state == "imbalance_up":
        return float(features["opening_range_midpoint"]) - buffer_price
    if state == "imbalance_down":
        return float(features["opening_range_midpoint"]) + buffer_price
    if state == "balance_high":
        return float(features["opening_range_high"]) + buffer_price
    if state == "balance_low":
        return float(features["opening_range_low"]) - buffer_price
    raise ValueError(f"No initial stop for state: {state}")


def _stop_fill(
    bar: pd.Series,
    *,
    direction: int,
    stop_trigger: float,
    slippage_price: float,
) -> float:
    if direction > 0:
        return min(stop_trigger, float(bar["bid_open"])) - slippage_price
    return max(stop_trigger, float(bar["ask_open"])) + slippage_price


def _timeout_fill(
    bar: pd.Series,
    *,
    direction: int,
    slippage_price: float,
) -> float:
    if direction > 0:
        return float(bar["bid_close"]) - slippage_price
    return float(bar["ask_close"]) + slippage_price


def _excursions(
    management: pd.DataFrame,
    *,
    direction: int,
    entry_price: float,
    pip_size: float,
) -> tuple[float, float]:
    if direction > 0:
        mfe = (float(management["bid_high"].max()) - entry_price) / pip_size
        mae = (entry_price - float(management["bid_low"].min())) / pip_size
    else:
        mfe = (entry_price - float(management["ask_low"].min())) / pip_size
        mae = (float(management["ask_high"].max()) - entry_price) / pip_size
    return max(0.0, mfe), max(0.0, mae)


def _manage_variant(
    management: pd.DataFrame,
    *,
    variant: str,
    direction: int,
    entry_price: float,
    initial_stop: float,
    initial_risk_pips: float,
    config: OpeningAuctionConfig,
    pip_size: float,
) -> dict[str, object]:
    execution = config.execution
    slippage_price = execution.slippage_per_side_pips * pip_size
    target_fill = (
        entry_price
        + direction * execution.target_r_multiple * initial_risk_pips * pip_size
    )
    target_trigger = target_fill + direction * slippage_price
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
        target_touched = variant == "fixed_2r" and (
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
            exit_price = target_trigger - direction * slippage_price
            exit_reason = "target_2r"
            break

        if variant == "trailing_session":
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
        "target_trigger_price": target_trigger if variant == "fixed_2r" else np.nan,
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


def _empty_event(event: object, *, sample_year: int) -> dict[str, object]:
    return {
        "sample_year": sample_year,
        "event_id": event.event_id,
        "session_name": event.session_name,
        "local_session_date": event.local_session_date,
        "event_timestamp_utc": event.event_timestamp_utc,
        "fx_trading_day": event.fx_trading_day,
        "phase1_eligible": bool(getattr(event, "eligible", False)),
        "value_eligible": bool(getattr(event, "value_eligible", False)),
        "value_state": getattr(event, "value_state", None),
        "previous_profile_day": getattr(event, "previous_profile_day", None),
        "auction_status": None,
        "auction_exclusion_reason": None,
        "cutoff_timestamp_utc": pd.NaT,
        "signal_timestamp_utc": pd.NaT,
        "entry_timestamp_utc": pd.NaT,
        "auction_state": None,
        "direction": np.nan,
        "side": None,
        "opening_mid": np.nan,
        "opening_range_high": np.nan,
        "opening_range_low": np.nan,
        "opening_range_midpoint": np.nan,
        "opening_range_pips": np.nan,
        "opening_close": np.nan,
        "displacement_pips": np.nan,
        "path_pips": np.nan,
        "efficiency": np.nan,
        "close_location": np.nan,
        "entry_price": np.nan,
        "entry_bid_open": np.nan,
        "entry_ask_open": np.nan,
        "entry_spread_pips": np.nan,
        "initial_stop_trigger_price": np.nan,
        "nominal_stop_fill_price": np.nan,
        "initial_risk_pips": np.nan,
        "trade_executed": False,
    }


def simulate_opening_auction(
    events: pd.DataFrame,
    bars: pd.DataFrame,
    config: OpeningAuctionConfig,
    sessions: SessionsConfig,
    *,
    pip_size: float,
    sample_year: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Classify and simulate each eligible London/New York opening."""

    ordered = bars.copy()
    ordered["timestamp"] = pd.to_datetime(ordered["timestamp"], utc=True)
    ordered = ordered.sort_values("timestamp", kind="stable").reset_index(drop=True)
    observation_bars = config.classification.observation_minutes // 5
    slippage_price = config.execution.slippage_per_side_pips * pip_size
    buffer_price = config.execution.stop_buffer_pips * pip_size
    event_rows: list[dict[str, object]] = []
    trade_rows: list[dict[str, object]] = []

    for event in events.itertuples(index=False):
        row = _empty_event(event, sample_year=sample_year)
        if not row["phase1_eligible"]:
            row.update(
                auction_status="excluded",
                auction_exclusion_reason="phase1_ineligible",
            )
            event_rows.append(row)
            continue

        opened = pd.Timestamp(event.event_timestamp_utc)
        cutoff = _session_cutoff(event, sessions)
        row["cutoff_timestamp_utc"] = cutoff
        if cutoff <= opened + timedelta(
            minutes=config.classification.observation_minutes
        ):
            row.update(
                auction_status="excluded",
                auction_exclusion_reason="invalid_session_cutoff",
            )
            event_rows.append(row)
            continue
        window = _exact_window(ordered, opened, cutoff)
        if window is None:
            row.update(
                auction_status="excluded",
                auction_exclusion_reason="incomplete_session_window",
            )
            event_rows.append(row)
            continue

        observation = window.iloc[:observation_bars]
        features = classify_opening_bars(observation, config)
        direction = features["direction"]
        row.update(
            **{
                key: value
                for key, value in features.items()
                if key
                not in {
                    "opening_range_price",
                    "displacement_price",
                    "path_price",
                }
            },
            opening_range_pips=float(features["opening_range_price"]) / pip_size,
            displacement_pips=float(features["displacement_price"]) / pip_size,
            path_pips=float(features["path_price"]) / pip_size,
            signal_timestamp_utc=opened
            + timedelta(minutes=config.classification.observation_minutes - 5),
            entry_timestamp_utc=opened
            + timedelta(minutes=config.classification.observation_minutes),
        )
        if direction is None:
            row.update(
                auction_status="no_direction",
                auction_exclusion_reason="degenerate_opening_auction",
            )
            event_rows.append(row)
            continue

        entry_bar = window.iloc[observation_bars]
        direction = int(direction)
        entry_price = _entry_price(
            entry_bar,
            direction=direction,
            slippage_price=slippage_price,
        )
        stop_trigger = _initial_stop(
            str(features["auction_state"]),
            features,
            buffer_price=buffer_price,
        )
        nominal_stop_fill = (
            stop_trigger - slippage_price
            if direction > 0
            else stop_trigger + slippage_price
        )
        initial_risk_pips = (
            direction * (entry_price - nominal_stop_fill) / pip_size
        )
        row.update(
            direction=direction,
            side="long" if direction > 0 else "short",
            entry_price=entry_price,
            entry_bid_open=float(entry_bar["bid_open"]),
            entry_ask_open=float(entry_bar["ask_open"]),
            entry_spread_pips=(
                float(entry_bar["ask_open"]) - float(entry_bar["bid_open"])
            )
            / pip_size,
            initial_stop_trigger_price=stop_trigger,
            nominal_stop_fill_price=nominal_stop_fill,
            initial_risk_pips=initial_risk_pips,
        )
        if initial_risk_pips <= 0:
            row.update(
                auction_status="excluded",
                auction_exclusion_reason="nonpositive_initial_risk",
            )
            event_rows.append(row)
            continue

        row.update(
            auction_status="traded",
            auction_exclusion_reason=None,
            trade_executed=True,
        )
        event_rows.append(row)
        management = window.iloc[observation_bars:].reset_index(drop=True)
        for variant in config.analysis.variants:
            managed = _manage_variant(
                management,
                variant=variant,
                direction=direction,
                entry_price=entry_price,
                initial_stop=stop_trigger,
                initial_risk_pips=initial_risk_pips,
                config=config,
                pip_size=pip_size,
            )
            trade_rows.append(
                {
                    **row,
                    "variant": variant,
                    **managed,
                    "holding_minutes": (
                        pd.Timestamp(managed["exit_timestamp_utc"])
                        - pd.Timestamp(row["entry_timestamp_utc"])
                    ).total_seconds()
                    / 60,
                }
            )

    return pd.DataFrame(event_rows), pd.DataFrame(trade_rows)
