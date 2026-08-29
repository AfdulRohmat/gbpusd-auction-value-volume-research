"""Point-in-time market-implied fundamental repricing features."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from gbpusd_research.config import FundamentalRepricingConfig
from gbpusd_research.features.fundamental_strength import CURRENCY_TIMEZONES


def _bundle_catalysts(
    policy_events: pd.DataFrame,
    macro_events: pd.DataFrame,
    *,
    start: date,
    end: date,
) -> pd.DataFrame:
    start_at = pd.Timestamp(start, tz="UTC")
    end_at = pd.Timestamp(end, tz="UTC")
    macro = macro_events[
        macro_events["available_at_utc"].ge(start_at)
        & macro_events["available_at_utc"].lt(end_at)
    ].copy()
    bundled = (
        macro.groupby(
            ["currency", "pillar", "available_at_utc"],
            sort=True,
            observed=True,
        )
        .agg(
            catalyst_id=(
                "event_id",
                lambda values: "+".join(sorted(map(str, values))),
            ),
            indicator=(
                "indicator",
                lambda values: "+".join(sorted(map(str, values))),
            ),
            reference_period=(
                "reference_period",
                lambda values: "+".join(sorted(set(map(str, values)))),
            ),
            catalyst_source_url=(
                "source_url",
                lambda values: "+".join(sorted(set(map(str, values)))),
            ),
        )
        .reset_index()
        .rename(columns={"available_at_utc": "release_at_utc"})
    )
    policy = policy_events.rename(
        columns={
            "event_id": "catalyst_id",
            "available_at_utc": "release_at_utc",
            "source_url": "catalyst_source_url",
        }
    )[
        [
            "catalyst_id",
            "currency",
            "pillar",
            "indicator",
            "reference_period",
            "release_at_utc",
            "catalyst_source_url",
        ]
    ]
    return pd.concat([policy, bundled], ignore_index=True).sort_values(
        ["release_at_utc", "currency", "pillar"], kind="stable"
    ).reset_index(drop=True)


def build_catalyst_yield_shocks(
    policy_events: pd.DataFrame,
    macro_events: pd.DataFrame,
    yields: pd.DataFrame,
    *,
    start: date,
    end: date,
) -> pd.DataFrame:
    """Map each registered catalyst to its same-day official 2Y yield change."""

    catalysts = _bundle_catalysts(
        policy_events,
        macro_events,
        start=start,
        end=end,
    )
    indexed_yields: dict[str, pd.DataFrame] = {}
    for currency, frame in yields.groupby("currency", observed=True):
        ordered = frame.sort_values("observation_date", kind="stable").copy()
        ordered["yield_observation_index"] = np.arange(len(ordered))
        indexed_yields[str(currency)] = ordered.set_index("observation_date")

    rows = []
    for catalyst in catalysts.itertuples(index=False):
        release_at = pd.Timestamp(catalyst.release_at_utc)
        local_date = (
            release_at.tz_convert(CURRENCY_TIMEZONES[catalyst.currency])
            .tz_localize(None)
            .normalize()
        )
        currency_yields = indexed_yields[catalyst.currency]
        row = catalyst._asdict()
        row["yield_observation_date"] = local_date
        if local_date not in currency_yields.index:
            rows.append(
                {
                    **row,
                    "yield_mapping_available": False,
                    "yield_mapping_reason": "missing_same_day_yield",
                }
            )
            continue
        current = currency_yields.loc[local_date]
        current_index = int(current["yield_observation_index"])
        if current_index == 0:
            rows.append(
                {
                    **row,
                    "yield_mapping_available": False,
                    "yield_mapping_reason": "missing_preceding_yield",
                }
            )
            continue
        previous = currency_yields.iloc[current_index - 1]
        shock_available = max(
            release_at,
            pd.Timestamp(current["available_at_utc"]),
        )
        rows.append(
            {
                **row,
                "yield_mapping_available": True,
                "yield_mapping_reason": None,
                "yield_observation_index": current_index,
                "yield_previous_observation_date": previous.name,
                "yield_2y_pct": float(current["yield_2y_pct"]),
                "yield_previous_2y_pct": float(previous["yield_2y_pct"]),
                "yield_shock_bps": (
                    float(current["yield_2y_pct"])
                    - float(previous["yield_2y_pct"])
                )
                * 100,
                "yield_available_at_utc": current["available_at_utc"],
                "shock_available_at_utc": shock_available,
                "yield_source_url": current["source_url"],
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["release_at_utc", "currency", "pillar"], kind="stable"
    ).reset_index(drop=True)


def _active_currency_shock(
    opened: pd.Timestamp,
    currency: str,
    shocks: pd.DataFrame,
    yields: pd.DataFrame,
    active_observations: int,
) -> dict[str, object] | None:
    prefix = currency.lower()
    available_yields = yields[
        yields["currency"].eq(currency)
        & yields["available_at_utc"].le(opened)
    ]
    if available_yields.empty:
        return None
    ordered_yields = available_yields.sort_values(
        "observation_date", kind="stable"
    )
    latest_yield_date = ordered_yields.iloc[-1]["observation_date"]
    full_currency_yields = yields[yields["currency"].eq(currency)].sort_values(
        "observation_date", kind="stable"
    )
    date_to_index = {
        observation_date: index
        for index, observation_date in enumerate(
            full_currency_yields["observation_date"]
        )
    }
    latest_yield_index = date_to_index[latest_yield_date]
    available_shocks = shocks[
        shocks["currency"].eq(currency)
        & shocks["yield_mapping_available"].eq(True)
        & shocks["shock_available_at_utc"].le(opened)
    ]
    inactive = {
        f"{prefix}_catalyst_active": False,
        f"{prefix}_catalyst_id": "inactive",
        f"{prefix}_catalyst_pillar": "inactive",
        f"{prefix}_catalyst_release_at": pd.NaT,
        f"{prefix}_shock_available_at": pd.NaT,
        f"{prefix}_yield_observation_date": pd.NaT,
        f"{prefix}_yield_previous_observation_date": pd.NaT,
        f"{prefix}_yield_observation_age": np.nan,
        f"{prefix}_yield_shock_bps": 0.0,
    }
    if available_shocks.empty:
        return inactive
    latest = available_shocks.sort_values(
        ["release_at_utc", "shock_available_at_utc"], kind="stable"
    ).iloc[-1]
    age = latest_yield_index - int(latest["yield_observation_index"])
    if age < 0:
        raise ValueError("Available shock cannot be newer than available yields")
    if age >= active_observations:
        return inactive
    return {
        f"{prefix}_catalyst_active": True,
        f"{prefix}_catalyst_id": latest["catalyst_id"],
        f"{prefix}_catalyst_pillar": latest["pillar"],
        f"{prefix}_catalyst_release_at": latest["release_at_utc"],
        f"{prefix}_shock_available_at": latest["shock_available_at_utc"],
        f"{prefix}_yield_observation_date": latest["yield_observation_date"],
        f"{prefix}_yield_previous_observation_date": latest[
            "yield_previous_observation_date"
        ],
        f"{prefix}_yield_observation_age": age,
        f"{prefix}_yield_shock_bps": float(latest["yield_shock_bps"]),
    }


def attach_relative_repricing_bias(
    events: pd.DataFrame,
    shocks: pd.DataFrame,
    yields: pd.DataFrame,
    config: FundamentalRepricingConfig,
) -> pd.DataFrame:
    """Attach the frozen relative event-day repricing bias to session opens."""

    rows = []
    for event in events.itertuples(index=False):
        opened = pd.Timestamp(event.event_timestamp_utc)
        gbp = _active_currency_shock(
            opened,
            "GBP",
            shocks,
            yields,
            config.signal.active_yield_observations,
        )
        usd = _active_currency_shock(
            opened,
            "USD",
            shocks,
            yields,
            config.signal.active_yield_observations,
        )
        if gbp is None or usd is None:
            rows.append(
                {
                    "repricing_available": False,
                    "repricing_unavailable_reason": "missing_yield_history",
                    "repricing_bias": np.nan,
                    "repricing_bias_label": "unavailable",
                }
            )
            continue
        relative = float(gbp["gbp_yield_shock_bps"]) - float(
            usd["usd_yield_shock_bps"]
        )
        threshold = config.signal.bias_threshold_bps
        bias = 1 if relative >= threshold else -1 if relative <= -threshold else 0
        label = "long" if bias > 0 else "short" if bias < 0 else "neutral"
        gbp_pillar = str(gbp["gbp_catalyst_pillar"])
        usd_pillar = str(usd["usd_catalyst_pillar"])
        active_pillars = {gbp_pillar, usd_pillar}.difference({"inactive"})
        signal_pillar = (
            "inactive"
            if not active_pillars
            else next(iter(active_pillars))
            if len(active_pillars) == 1
            else "mixed"
        )
        rows.append(
            {
                "repricing_available": True,
                "repricing_unavailable_reason": None,
                "repricing_model": "relative_event_day_2y_repricing_v1",
                **gbp,
                **usd,
                "repricing_relative_shock_bps": relative,
                "repricing_bias": bias,
                "repricing_bias_label": label,
                "repricing_signal_pillar": signal_pillar,
                "repricing_regime_id": (
                    f"{gbp['gbp_catalyst_id']}|{usd['usd_catalyst_id']}"
                ),
            }
        )
    return pd.concat([events.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
