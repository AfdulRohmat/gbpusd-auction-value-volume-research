"""Validated official point-in-time macro ledgers for Phase 3 research."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from gbpusd_research.config import (
    FundamentalBiasConfig,
    FundamentalRepricingConfig,
    FundamentalStrengthConfig,
)
from gbpusd_research.utils.paths import resolve_within_project

POLICY_COLUMNS = {
    "event_id",
    "currency",
    "available_at_utc",
    "rate_lower_pct",
    "rate_upper_pct",
    "source_url",
}
OFFICIAL_DOMAINS = {
    "GBP": "bankofengland.co.uk",
    "USD": "federalreserve.gov",
}
MACRO_COLUMNS = {
    "event_id",
    "currency",
    "pillar",
    "indicator",
    "reference_period",
    "available_at_utc",
    "value",
    "unit",
    "source_url",
}
EXPECTED_INDICATORS = {
    "GBP": {"headline_cpi_yoy", "core_cpi_yoy", "regular_earnings_yoy"},
    "USD": {
        "headline_cpi_yoy",
        "core_cpi_yoy",
        "average_hourly_earnings_yoy",
    },
}
MACRO_DOMAINS = {
    "GBP": "ons.gov.uk",
    "USD": "bls.gov",
}
YIELD_COLUMNS = {
    "currency",
    "observation_date",
    "yield_2y_pct",
    "unit",
    "source_url",
}
YIELD_DOMAINS = {
    "GBP": "bankofengland.co.uk",
    "USD": "home.treasury.gov",
}
POLICY_DECISION_COLUMNS = {
    "event_id",
    "currency",
    "pillar",
    "indicator",
    "reference_period",
    "available_at_utc",
    "source_url",
}


def _validate_columns(
    frame: pd.DataFrame, expected: set[str], *, ledger_name: str
) -> None:
    missing = sorted(expected.difference(frame.columns))
    extra = sorted(set(frame.columns).difference(expected))
    if not missing and not extra:
        return
    details = []
    if missing:
        details.append("missing: " + ", ".join(missing))
    if extra:
        details.append("unexpected: " + ", ".join(extra))
    raise ValueError(
        f"Invalid {ledger_name} ledger columns (" + "; ".join(details) + ")"
    )


def _load_policy_rate_events_path(
    project_root: Path, events_path: Path
) -> pd.DataFrame:
    path = resolve_within_project(project_root, events_path)
    try:
        events = pd.read_csv(path)
    except FileNotFoundError as exc:
        raise ValueError(f"Policy event ledger not found: {path}") from exc
    _validate_columns(events, POLICY_COLUMNS, ledger_name="policy")
    if events.empty:
        raise ValueError("Policy event ledger must not be empty")
    if events["event_id"].isna().any() or events["event_id"].duplicated().any():
        raise ValueError("Policy event_id values must be present and unique")
    if set(events["currency"]) != set(OFFICIAL_DOMAINS):
        raise ValueError("Policy ledger must contain exactly GBP and USD events")
    events["available_at_utc"] = pd.to_datetime(
        events["available_at_utc"], utc=True, errors="raise"
    )
    for column in ("rate_lower_pct", "rate_upper_pct"):
        events[column] = pd.to_numeric(events[column], errors="raise")
        if events[column].isna().any() or events[column].lt(0).any():
            raise ValueError(f"{column} must contain non-negative rates")
    if events["rate_lower_pct"].gt(events["rate_upper_pct"]).any():
        raise ValueError("Policy rate lower bound must not exceed upper bound")
    for currency, domain in OFFICIAL_DOMAINS.items():
        subset = events[events["currency"].eq(currency)]
        if not subset["available_at_utc"].is_monotonic_increasing:
            raise ValueError(f"{currency} policy events must be timestamp-sorted")
        pattern = rf"^https://(www\.)?{re.escape(domain)}/"
        if not subset["source_url"].str.match(pattern).all():
            raise ValueError(f"{currency} policy events require official source URLs")
    events["rate_mid_pct"] = (
        events["rate_lower_pct"] + events["rate_upper_pct"]
    ) / 2
    return events.sort_values("available_at_utc", kind="stable").reset_index(drop=True)


def load_policy_rate_events(
    project_root: Path, config: FundamentalBiasConfig
) -> pd.DataFrame:
    """Load and validate the immutable policy-decision ledger."""

    return _load_policy_rate_events_path(project_root, config.policy.events_path)


def load_strength_policy_rate_events(
    project_root: Path, config: FundamentalStrengthConfig
) -> pd.DataFrame:
    """Load the Phase-3B policy ledger with two pre-period observations."""

    return _load_policy_rate_events_path(project_root, config.data.policy_events_path)


def load_macro_release_events(
    project_root: Path,
    config: FundamentalStrengthConfig | FundamentalRepricingConfig,
) -> pd.DataFrame:
    """Load archived release-time CPI and earnings observations."""

    path = resolve_within_project(project_root, config.data.macro_events_path)
    try:
        events = pd.read_csv(path)
    except FileNotFoundError as exc:
        raise ValueError(f"Macro release ledger not found: {path}") from exc
    _validate_columns(events, MACRO_COLUMNS, ledger_name="macro release")
    if events.empty:
        raise ValueError("Macro release ledger must not be empty")
    if events["event_id"].isna().any() or events["event_id"].duplicated().any():
        raise ValueError("Macro event_id values must be present and unique")
    if events[["reference_period", "source_url"]].isna().any().any():
        raise ValueError("Macro reference periods and source URLs must be present")
    if set(events["currency"]) != set(EXPECTED_INDICATORS):
        raise ValueError("Macro ledger must contain exactly GBP and USD events")
    events["available_at_utc"] = pd.to_datetime(
        events["available_at_utc"], utc=True, errors="raise"
    )
    events["value"] = pd.to_numeric(events["value"], errors="raise")
    if events["value"].isna().any() or events["value"].lt(0).any():
        raise ValueError("Macro values must be non-negative numbers")
    if set(events["pillar"]) != {"inflation", "labor"}:
        raise ValueError("Macro pillars must be exactly inflation and labor")
    if set(events["unit"]) != {"percent_yoy"}:
        raise ValueError("Macro values must use percent_yoy")
    for currency, domain in MACRO_DOMAINS.items():
        subset = events[events["currency"].eq(currency)]
        if set(subset["indicator"]) != EXPECTED_INDICATORS[currency]:
            raise ValueError(f"{currency} macro indicators do not match the contract")
        pattern = rf"^https://(www\.)?{re.escape(domain)}/"
        if not subset["source_url"].str.match(pattern).all():
            raise ValueError(f"{currency} macro events require official source URLs")
        for indicator, series in subset.groupby("indicator", observed=True):
            if len(series) < 2:
                raise ValueError(f"{currency} {indicator} requires two releases")
            if not series["available_at_utc"].is_monotonic_increasing:
                raise ValueError(
                    f"{currency} {indicator} releases must be timestamp-sorted"
                )
    return events.sort_values("available_at_utc", kind="stable").reset_index(drop=True)


def _load_two_year_yields_path(
    project_root: Path,
    yields_path: Path,
    *,
    minimum_observations: int,
) -> pd.DataFrame:
    path = resolve_within_project(project_root, yields_path)
    try:
        yields = pd.read_csv(path)
    except FileNotFoundError as exc:
        raise ValueError(f"Two-year yield ledger not found: {path}") from exc
    _validate_columns(yields, YIELD_COLUMNS, ledger_name="two-year yield")
    if yields.empty:
        raise ValueError("Two-year yield ledger must not be empty")
    if set(yields["currency"]) != set(YIELD_DOMAINS):
        raise ValueError("Yield ledger must contain exactly GBP and USD")
    yields["observation_date"] = pd.to_datetime(
        yields["observation_date"], errors="raise"
    ).dt.normalize()
    yields["yield_2y_pct"] = pd.to_numeric(yields["yield_2y_pct"], errors="raise")
    if yields["yield_2y_pct"].isna().any() or yields["yield_2y_pct"].lt(0).any():
        raise ValueError("Two-year yields must be non-negative numbers")
    if set(yields["unit"]) != {"percent"}:
        raise ValueError("Two-year yields must use percent")
    for currency, domain in YIELD_DOMAINS.items():
        subset = yields[yields["currency"].eq(currency)]
        if len(subset) <= minimum_observations:
            raise ValueError(f"{currency} yield history is too short")
        if subset["observation_date"].duplicated().any():
            raise ValueError(f"{currency} yield dates must be unique")
        if not subset["observation_date"].is_monotonic_increasing:
            raise ValueError(f"{currency} yield dates must be sorted")
        pattern = rf"^https://(www\.)?{re.escape(domain)}/"
        if not subset["source_url"].str.match(pattern).all():
            raise ValueError(f"{currency} yields require official source URLs")
    yields = yields.sort_values(
        ["currency", "observation_date"], kind="stable"
    ).reset_index(drop=True)
    availability = pd.Series(pd.NaT, index=yields.index, dtype="datetime64[ns, UTC]")
    gbp_mask = yields["currency"].eq("GBP")
    gbp_dates = yields.loc[gbp_mask, "observation_date"]
    next_gbp_date = gbp_dates.shift(-1)
    next_gbp_date.iloc[-1] = gbp_dates.iloc[-1] + pd.offsets.BDay(1)
    availability.loc[gbp_mask] = (
        (next_gbp_date + pd.offsets.Hour(12))
        .dt.tz_localize("Europe/London")
        .dt.tz_convert("UTC")
    )
    usd_mask = yields["currency"].eq("USD")
    availability.loc[usd_mask] = (
        (yields.loc[usd_mask, "observation_date"] + pd.offsets.Hour(18))
        .dt.tz_localize("America/New_York")
        .dt.tz_convert("UTC")
    )
    yields["available_at_utc"] = availability
    return yields


def load_two_year_yields(
    project_root: Path, config: FundamentalStrengthConfig
) -> pd.DataFrame:
    """Load official daily two-year yield observations for Phase 3B."""

    return _load_two_year_yields_path(
        project_root,
        config.data.yields_path,
        minimum_observations=config.scoring.yield_lookback_observations,
    )


def load_repricing_two_year_yields(
    project_root: Path, config: FundamentalRepricingConfig
) -> pd.DataFrame:
    """Load official daily two-year yields for event-day repricing."""

    return _load_two_year_yields_path(
        project_root,
        config.data.yields_path,
        minimum_observations=config.signal.active_yield_observations,
    )


def load_policy_decision_events(
    project_root: Path, config: FundamentalRepricingConfig
) -> pd.DataFrame:
    """Load all registered 2024 BoE and Federal Reserve decisions."""

    path = resolve_within_project(project_root, config.data.policy_decisions_path)
    try:
        events = pd.read_csv(path)
    except FileNotFoundError as exc:
        raise ValueError(f"Policy decision ledger not found: {path}") from exc
    _validate_columns(events, POLICY_DECISION_COLUMNS, ledger_name="policy decision")
    if events.empty:
        raise ValueError("Policy decision ledger must not be empty")
    if events["event_id"].isna().any() or events["event_id"].duplicated().any():
        raise ValueError("Policy decision event_id values must be present and unique")
    if set(events["currency"]) != set(OFFICIAL_DOMAINS):
        raise ValueError("Policy decision ledger must contain exactly GBP and USD")
    if not events["pillar"].eq("policy").all():
        raise ValueError("Policy decision pillar must be policy")
    if not events["indicator"].eq("policy_decision").all():
        raise ValueError("Policy decision indicator must be policy_decision")
    events["available_at_utc"] = pd.to_datetime(
        events["available_at_utc"], utc=True, errors="raise"
    )
    events["reference_period"] = pd.to_datetime(
        events["reference_period"], errors="raise"
    ).dt.strftime("%Y-%m-%d")
    if not events["available_at_utc"].dt.year.eq(2024).all():
        raise ValueError("Policy decision ledger is frozen to 2024")
    for currency, domain in OFFICIAL_DOMAINS.items():
        subset = events[events["currency"].eq(currency)]
        if len(subset) != 8:
            raise ValueError(f"{currency} requires all eight 2024 policy decisions")
        if not subset["available_at_utc"].is_monotonic_increasing:
            raise ValueError(f"{currency} policy decisions must be timestamp-sorted")
        pattern = rf"^https://(www\.)?{re.escape(domain)}/"
        if not subset["source_url"].str.match(pattern).all():
            raise ValueError(
                f"{currency} policy decisions require official source URLs"
            )
    return events.sort_values("available_at_utc", kind="stable").reset_index(
        drop=True
    )
