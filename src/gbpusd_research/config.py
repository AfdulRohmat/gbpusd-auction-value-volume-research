"""Strict configuration models and YAML loading."""

from __future__ import annotations

from datetime import date, time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Base model that rejects misspelled keys and remains immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown IANA timezone: {value}") from exc
    return value


class InstrumentConfig(StrictModel):
    symbol: Literal["GBPUSD"]
    pip_size: float = Field(gt=0)
    price_decimals: int = Field(ge=1, le=10)


class DataPathsConfig(StrictModel):
    raw: Path
    interim: Path
    processed: Path


class DataConfig(StrictModel):
    source: Literal["histdata"]
    raw_frequency: Literal["tick"]
    output_frequency: Literal["5min"]
    start: date
    end: date
    paths: DataPathsConfig

    @model_validator(mode="after")
    def validate_date_range(self) -> DataConfig:
        if self.end <= self.start:
            raise ValueError(
                "data.end must be later than data.start (end is exclusive)"
            )
        return self


class QualityConfig(StrictModel):
    reject_crossed_quotes: bool
    max_spread_pips_warning: float = Field(gt=0)
    event_min_coverage_ratio: float = Field(gt=0, le=1)
    exclude_weekends: bool


class StudyConfig(StrictModel):
    horizons_minutes: tuple[int, ...]
    preopen_windows_minutes: tuple[int, ...]
    random_seed: int = Field(ge=0)
    bootstrap_resamples: int = Field(ge=100)
    confidence_level: float = Field(gt=0, lt=1)

    @model_validator(mode="after")
    def validate_windows(self) -> StudyConfig:
        for name, values in (
            ("horizons_minutes", self.horizons_minutes),
            ("preopen_windows_minutes", self.preopen_windows_minutes),
        ):
            if not values or any(value <= 0 or value % 5 for value in values):
                raise ValueError(f"{name} must contain positive M5-aligned values")
            if tuple(sorted(set(values))) != values:
                raise ValueError(f"{name} must be sorted and unique")
        return self


class ResearchConfig(StrictModel):
    instrument: InstrumentConfig
    data: DataConfig
    quality: QualityConfig
    study: StudyConfig


class TradingDayConfig(StrictModel):
    timezone: str
    boundary: time

    @model_validator(mode="after")
    def validate_timezone_name(self) -> TradingDayConfig:
        _validate_timezone(self.timezone)
        return self


class SessionConfig(StrictModel):
    timezone: str
    open: time
    study_minutes: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_values(self) -> SessionConfig:
        _validate_timezone(self.timezone)
        if self.study_minutes % 5:
            raise ValueError("session study_minutes must be M5-aligned")
        return self


class ControlsConfig(StrictModel):
    exclusion_minutes_around_session_open: int = Field(ge=0)
    samples_per_event: int = Field(ge=1)
    matching: tuple[Literal["weekday", "calendar_month", "local_start_time_pool"], ...]
    fixed_local_times: dict[str, time]


class SessionsConfig(StrictModel):
    trading_day: TradingDayConfig
    sessions: dict[str, SessionConfig]
    controls: ControlsConfig

    @model_validator(mode="after")
    def validate_sessions(self) -> SessionsConfig:
        if not self.sessions:
            raise ValueError("At least one session must be configured")
        if any(not name.strip() for name in self.sessions):
            raise ValueError("Session names must not be blank")
        unknown_controls = set(self.controls.fixed_local_times).difference(
            self.sessions
        )
        if unknown_controls:
            raise ValueError(
                "Fixed control time references unknown session(s): "
                + ", ".join(sorted(unknown_controls))
            )
        missing_controls = set(self.sessions).difference(
            self.controls.fixed_local_times
        )
        if missing_controls:
            raise ValueError(
                "Missing fixed control time for session(s): "
                + ", ".join(sorted(missing_controls))
            )
        return self


class ProjectConfig(StrictModel):
    research: ResearchConfig
    sessions: SessionsConfig


class ProfileConfig(StrictModel):
    bin_size_pips: float = Field(gt=0)
    value_area_fraction: float = Field(gt=0, le=1)
    minimum_m5_coverage_ratio: float = Field(gt=0, le=1)


class VwapConfig(StrictModel):
    slope_minutes: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_slope(self) -> VwapConfig:
        if self.slope_minutes % 5:
            raise ValueError("vwap.slope_minutes must be M5-aligned")
        return self


class ClassificationConfig(StrictModel):
    boundary_buffer_pips: float = Field(ge=0)
    acceptance_consecutive_closes: int = Field(ge=2)
    transition_horizons_minutes: tuple[int, ...]

    @model_validator(mode="after")
    def validate_horizons(self) -> ClassificationConfig:
        values = self.transition_horizons_minutes
        if not values or any(value <= 0 or value % 5 for value in values):
            raise ValueError(
                "classification.transition_horizons_minutes must be positive and "
                "M5-aligned"
            )
        if tuple(sorted(set(values))) != values:
            raise ValueError(
                "classification.transition_horizons_minutes must be sorted and unique"
            )
        return self


class ValueResearchGateConfig(StrictModel):
    minimum_feature_coverage_ratio: float = Field(gt=0, le=1)
    minimum_group_size: int = Field(ge=2)
    materiality_pips: float = Field(gt=0)


class ValueStateConfig(StrictModel):
    profile: ProfileConfig
    vwap: VwapConfig
    classification: ClassificationConfig
    research_gate: ValueResearchGateConfig


class PolicyBiasConfig(StrictModel):
    events_path: Path
    impulse_lookback_days: int = Field(ge=1)


class FundamentalAnalysisConfig(StrictModel):
    horizons_minutes: tuple[int, ...]
    minimum_feature_coverage_ratio: float = Field(gt=0, le=1)
    minimum_group_size: int = Field(ge=2)
    minimum_direction_months: int = Field(ge=1)
    materiality_pips: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_horizons(self) -> FundamentalAnalysisConfig:
        values = self.horizons_minutes
        if not values or any(value <= 0 or value % 5 for value in values):
            raise ValueError(
                "analysis.horizons_minutes must be positive and M5-aligned"
            )
        if tuple(sorted(set(values))) != values:
            raise ValueError("analysis.horizons_minutes must be sorted and unique")
        return self


class FundamentalBiasConfig(StrictModel):
    policy: PolicyBiasConfig
    analysis: FundamentalAnalysisConfig


class FundamentalStrengthDataConfig(StrictModel):
    policy_events_path: Path
    macro_events_path: Path
    yields_path: Path


class FundamentalStrengthWeightsConfig(StrictModel):
    policy: int = Field(ge=1)
    inflation: int = Field(ge=1)
    labor: int = Field(ge=1)
    yield_expectation: int = Field(ge=1)


class FundamentalStrengthScoringConfig(StrictModel):
    yield_lookback_observations: int = Field(ge=1)
    yield_deadband_pct: float = Field(gt=0)
    primary_bias_threshold: int = Field(ge=1, le=8)
    weighted_bias_threshold: int = Field(ge=1)
    robustness_weights: FundamentalStrengthWeightsConfig


class FundamentalStrengthConfig(StrictModel):
    data: FundamentalStrengthDataConfig
    scoring: FundamentalStrengthScoringConfig
    analysis: FundamentalAnalysisConfig


class FundamentalRepricingDataConfig(StrictModel):
    policy_decisions_path: Path
    macro_events_path: Path
    yields_path: Path


class FundamentalRepricingSignalConfig(StrictModel):
    active_yield_observations: int = Field(ge=1)
    bias_threshold_bps: float = Field(gt=0)


class FundamentalRepricingAnalysisConfig(StrictModel):
    horizons_session_days: tuple[int, ...]
    primary_horizon_session_days: int = Field(ge=1)
    bootstrap_resamples: int = Field(ge=100)
    familywise_confidence_level: float = Field(gt=0, lt=1)
    minimum_directional_regimes: int = Field(ge=2)
    minimum_regimes_per_direction: int = Field(ge=1)
    materiality_pips: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_horizons(self) -> FundamentalRepricingAnalysisConfig:
        values = self.horizons_session_days
        if not values or any(value <= 0 for value in values):
            raise ValueError(
                "analysis.horizons_session_days must contain positive values"
            )
        if tuple(sorted(set(values))) != values:
            raise ValueError(
                "analysis.horizons_session_days must be sorted and unique"
            )
        if self.primary_horizon_session_days not in values:
            raise ValueError(
                "analysis.primary_horizon_session_days must be registered"
            )
        if self.minimum_directional_regimes < 2 * self.minimum_regimes_per_direction:
            raise ValueError(
                "minimum_directional_regimes must cover both direction minima"
            )
        return self


class FundamentalRepricingConfig(StrictModel):
    data: FundamentalRepricingDataConfig
    signal: FundamentalRepricingSignalConfig
    analysis: FundamentalRepricingAnalysisConfig


class OpeningValueExecutionConfig(StrictModel):
    entry_deadline_minutes: int = Field(ge=5)
    timeout_minutes: int = Field(gt=0)
    stop_buffer_pips: float = Field(gt=0)
    slippage_per_side_pips: float = Field(ge=0)
    stress_slippage_per_side_pips: float = Field(ge=0)
    intrabar_priority: Literal["stop_first"]

    @model_validator(mode="after")
    def validate_execution(self) -> OpeningValueExecutionConfig:
        if self.entry_deadline_minutes % 5 or self.timeout_minutes % 5:
            raise ValueError("Phase-4 execution minutes must be M5-aligned")
        if self.entry_deadline_minutes >= self.timeout_minutes:
            raise ValueError("entry deadline must precede the hard timeout")
        if self.stress_slippage_per_side_pips < self.slippage_per_side_pips:
            raise ValueError("stress slippage must not be below primary slippage")
        return self


class OpeningValueAnalysisConfig(StrictModel):
    primary_session: Literal["new_york"]
    replication_session: Literal["london"]
    bootstrap_resamples: int = Field(ge=100)
    confidence_level: float = Field(gt=0, lt=1)
    minimum_value_feature_coverage_ratio: float = Field(gt=0, le=1)
    minimum_trades: int = Field(ge=2)
    minimum_trades_per_direction: int = Field(ge=1)
    minimum_active_months: int = Field(ge=1, le=12)
    minimum_expectancy_r: float = Field(gt=0)
    minimum_profit_factor: float = Field(gt=1)
    maximum_drawdown_r: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_counts(self) -> OpeningValueAnalysisConfig:
        if self.minimum_trades < 2 * self.minimum_trades_per_direction:
            raise ValueError("minimum trades must cover both direction minima")
        return self


class OpeningValueStrategyConfig(StrictModel):
    execution: OpeningValueExecutionConfig
    analysis: OpeningValueAnalysisConfig


OpeningAblationVariant = Literal[
    "open_timeout_30",
    "open_timeout_60",
    "open_timeout_90",
    "open_boundary_90",
    "open_poc_90",
    "signal_cohort_open_timeout_90",
    "confirmed_timeout_all",
    "confirmed_timeout_favorable",
    "confirmed_poc_no_stop",
    "phase4_full",
]


class OpeningAblationAnalysisConfig(StrictModel):
    variants: tuple[OpeningAblationVariant, ...]
    bootstrap_resamples: int = Field(ge=100)
    confidence_level: float = Field(gt=0, lt=1)
    minimum_events_for_interpretation: int = Field(ge=2)

    @model_validator(mode="after")
    def validate_variants(self) -> OpeningAblationAnalysisConfig:
        if not self.variants:
            raise ValueError("Phase-5 ablation variants must not be empty")
        if len(set(self.variants)) != len(self.variants):
            raise ValueError("Phase-5 ablation variants must be unique")
        return self


class OpeningAblationConfig(StrictModel):
    analysis: OpeningAblationAnalysisConfig


def _read_yaml(path: Path) -> object:
    try:
        with path.open(encoding="utf-8") as stream:
            content = yaml.safe_load(stream)
    except FileNotFoundError as exc:
        raise ValueError(f"Configuration file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(content, dict):
        raise ValueError(f"Configuration root must be a mapping: {path}")
    return content


def load_project_config(research_path: Path, sessions_path: Path) -> ProjectConfig:
    """Load and strictly validate the two project configuration files."""

    return ProjectConfig(
        research=ResearchConfig.model_validate(_read_yaml(research_path)),
        sessions=SessionsConfig.model_validate(_read_yaml(sessions_path)),
    )


def load_value_state_config(path: Path) -> ValueStateConfig:
    """Load and strictly validate Phase-2 value-state configuration."""

    return ValueStateConfig.model_validate(_read_yaml(path))


def load_fundamental_bias_config(path: Path) -> FundamentalBiasConfig:
    """Load and strictly validate Phase-3 fundamental-bias configuration."""

    return FundamentalBiasConfig.model_validate(_read_yaml(path))


def load_fundamental_strength_config(path: Path) -> FundamentalStrengthConfig:
    """Load and strictly validate Phase-3B relative-strength configuration."""

    return FundamentalStrengthConfig.model_validate(_read_yaml(path))


def load_fundamental_repricing_config(path: Path) -> FundamentalRepricingConfig:
    """Load and strictly validate Phase-3C repricing configuration."""

    return FundamentalRepricingConfig.model_validate(_read_yaml(path))


def load_opening_value_strategy_config(path: Path) -> OpeningValueStrategyConfig:
    """Load and strictly validate Phase-4 opening-value strategy configuration."""

    return OpeningValueStrategyConfig.model_validate(_read_yaml(path))


def load_opening_ablation_config(path: Path) -> OpeningAblationConfig:
    """Load and strictly validate the Phase-5 ablation configuration."""

    return OpeningAblationConfig.model_validate(_read_yaml(path))
