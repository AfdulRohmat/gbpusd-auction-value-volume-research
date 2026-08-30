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


OpeningAuctionExitVariant = Literal[
    "fixed_2r",
    "session_hold",
    "trailing_session",
]


class OpeningAuctionClassificationConfig(StrictModel):
    observation_minutes: int = Field(gt=0)
    imbalance_efficiency_threshold: float = Field(gt=0, le=1)
    extreme_close_fraction: float = Field(gt=0.5, lt=1)

    @model_validator(mode="after")
    def validate_observation_window(self) -> OpeningAuctionClassificationConfig:
        if self.observation_minutes % 5:
            raise ValueError("observation_minutes must be M5-aligned")
        return self


class OpeningAuctionExecutionConfig(StrictModel):
    stop_buffer_pips: float = Field(gt=0)
    target_r_multiple: float = Field(gt=0)
    slippage_per_side_pips: float = Field(ge=0)
    intrabar_priority: Literal["stop_first"]
    london_cutoff: Literal["new_york_open"]
    new_york_cutoff: Literal["fx_day_boundary"]


class OpeningAuctionTrailingConfig(StrictModel):
    break_even_trigger_r: float = Field(gt=0)
    swing_bars: int = Field(ge=1)
    buffer_pips: float = Field(ge=0)


class OpeningAuctionAnalysisConfig(StrictModel):
    variants: tuple[OpeningAuctionExitVariant, ...]
    bootstrap_resamples: int = Field(ge=100)
    confidence_level: float = Field(gt=0, lt=1)
    minimum_events_for_interpretation: int = Field(ge=2)
    benchmark_expectancy_r: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_variants(self) -> OpeningAuctionAnalysisConfig:
        required = {"fixed_2r", "session_hold", "trailing_session"}
        if set(self.variants) != required or len(self.variants) != len(required):
            raise ValueError(
                "Phase-6 variants must contain fixed_2r, session_hold, and "
                "trailing_session exactly once"
            )
        return self


class OpeningAuctionConfig(StrictModel):
    classification: OpeningAuctionClassificationConfig
    execution: OpeningAuctionExecutionConfig
    trailing: OpeningAuctionTrailingConfig
    analysis: OpeningAuctionAnalysisConfig


class AuctionTaxonomyStateConfig(StrictModel):
    window_minutes: int = Field(gt=0)
    balance_max_efficiency: float = Field(ge=0, lt=1)
    balance_min_overlap: float = Field(gt=0, le=1)
    balance_min_midpoint_crossings: int = Field(ge=1)
    imbalance_min_efficiency: float = Field(gt=0, le=1)
    imbalance_min_directional_persistence: float = Field(gt=0, le=1)
    extreme_close_fraction: float = Field(gt=0.5, lt=1)
    confirmation_windows: int = Field(ge=2)

    @model_validator(mode="after")
    def validate_state_thresholds(self) -> AuctionTaxonomyStateConfig:
        if self.window_minutes % 5:
            raise ValueError("taxonomy.state.window_minutes must be M5-aligned")
        if self.balance_max_efficiency >= self.imbalance_min_efficiency:
            raise ValueError(
                "balance efficiency threshold must be below imbalance threshold"
            )
        return self


class AuctionTaxonomyActivityConfig(StrictModel):
    baseline_bars: int = Field(ge=12)
    minimum_baseline_bars: int = Field(ge=6)
    quiet_ratio_max: float = Field(gt=0, lt=1)
    active_ratio_min: float = Field(gt=1)

    @model_validator(mode="after")
    def validate_activity_baseline(self) -> AuctionTaxonomyActivityConfig:
        if self.minimum_baseline_bars > self.baseline_bars:
            raise ValueError("minimum activity baseline cannot exceed baseline_bars")
        return self


class AuctionTaxonomyTransitionConfig(StrictModel):
    boundary_test_tolerance_pips: float = Field(gt=0)
    activity_burst_ratio: float = Field(gt=1)
    opening_window_minutes: int = Field(gt=0)
    horizons_minutes: tuple[int, ...]
    balance_age_bins_minutes: tuple[int, ...]

    @model_validator(mode="after")
    def validate_transition_windows(self) -> AuctionTaxonomyTransitionConfig:
        horizons = self.horizons_minutes
        if not horizons or any(value <= 0 or value % 5 for value in horizons):
            raise ValueError("transition horizons must be positive and M5-aligned")
        if tuple(sorted(set(horizons))) != horizons:
            raise ValueError("transition horizons must be sorted and unique")
        bins = self.balance_age_bins_minutes
        if not bins or bins[0] != 0 or any(value < 0 for value in bins):
            raise ValueError("balance age bins must start at zero")
        if tuple(sorted(set(bins))) != bins:
            raise ValueError("balance age bins must be sorted and unique")
        return self


class AuctionTaxonomyAnalysisConfig(StrictModel):
    controls: tuple[Literal["fixed", "matched"], ...]
    minimum_episodes_for_interpretation: int = Field(ge=2)
    confidence_level: float = Field(gt=0, lt=1)

    @model_validator(mode="after")
    def validate_controls(self) -> AuctionTaxonomyAnalysisConfig:
        if set(self.controls) != {"fixed", "matched"} or len(self.controls) != 2:
            raise ValueError("Phase-7 controls must contain fixed and matched once")
        return self


class AuctionTaxonomyConfig(StrictModel):
    state: AuctionTaxonomyStateConfig
    activity: AuctionTaxonomyActivityConfig
    transition: AuctionTaxonomyTransitionConfig
    analysis: AuctionTaxonomyAnalysisConfig


BalanceBoundarySetupVariant = Literal[
    "rotation_midpoint",
    "acceptance_fixed_2r",
    "acceptance_trailing_session",
]
BalanceBoundaryPortfolioVariant = Literal[
    "combined_fixed_2r",
    "combined_trailing_session",
]


class BalanceBoundaryContextConfig(StrictModel):
    signal_window_minutes: int = Field(gt=0)
    boundary_touch_tolerance_pips: float = Field(ge=0)
    rejection_close_inside_pips: float = Field(ge=0)
    acceptance_close_outside_pips: float = Field(gt=0)
    acceptance_consecutive_closes: Literal[2]
    rejection_raw_states: tuple[Literal["balance", "transition"], ...]

    @model_validator(mode="after")
    def validate_context(self) -> BalanceBoundaryContextConfig:
        if self.signal_window_minutes % 5:
            raise ValueError("Phase-8 signal window must be M5-aligned")
        required = {"balance", "transition"}
        if set(self.rejection_raw_states) != required or len(
            self.rejection_raw_states
        ) != len(required):
            raise ValueError(
                "Phase-8 rejection raw states must contain balance and transition"
            )
        return self


class BalanceBoundaryExecutionConfig(StrictModel):
    stop_buffer_pips: float = Field(gt=0)
    minimum_rotation_reward_to_risk: float = Field(gt=0)
    breakout_target_r_multiple: float = Field(gt=0)
    slippage_per_side_pips: float = Field(ge=0)
    intrabar_priority: Literal["stop_first"]
    london_cutoff: Literal["new_york_open"]
    new_york_cutoff: Literal["fx_day_boundary"]


class BalanceBoundaryTrailingConfig(StrictModel):
    break_even_trigger_r: float = Field(gt=0)
    swing_bars: int = Field(ge=1)
    buffer_pips: float = Field(ge=0)


class BalanceBoundaryAnalysisConfig(StrictModel):
    setup_variants: tuple[BalanceBoundarySetupVariant, ...]
    portfolio_variants: tuple[BalanceBoundaryPortfolioVariant, ...]
    bootstrap_resamples: int = Field(ge=100)
    confidence_level: float = Field(gt=0, lt=1)
    minimum_events_for_interpretation: int = Field(ge=2)
    benchmark_expectancy_r: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_variants(self) -> BalanceBoundaryAnalysisConfig:
        setup = {
            "rotation_midpoint",
            "acceptance_fixed_2r",
            "acceptance_trailing_session",
        }
        portfolio = {"combined_fixed_2r", "combined_trailing_session"}
        if set(self.setup_variants) != setup or len(self.setup_variants) != len(setup):
            raise ValueError("Phase-8 setup variants must match the frozen set")
        if set(self.portfolio_variants) != portfolio or len(
            self.portfolio_variants
        ) != len(portfolio):
            raise ValueError("Phase-8 portfolio variants must match the frozen set")
        return self


class BalanceBoundaryStrategyConfig(StrictModel):
    context: BalanceBoundaryContextConfig
    execution: BalanceBoundaryExecutionConfig
    trailing: BalanceBoundaryTrailingConfig
    analysis: BalanceBoundaryAnalysisConfig


ExnessSourcePreference = Literal["mt5_account_export", "personal_area_archive"]
ExnessModelVariant = Literal["price_only", "activity_only", "price_activity"]


class ExnessQuoteDataConfig(StrictModel):
    input_path: Path
    processed_path: Path
    source_preference: ExnessSourcePreference
    source_timezone: Literal["UTC"]
    accepted_symbols: tuple[str, ...]
    pip_size: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_symbols(self) -> ExnessQuoteDataConfig:
        if not self.accepted_symbols or any(
            not symbol.strip() for symbol in self.accepted_symbols
        ):
            raise ValueError("accepted_symbols must contain non-blank symbols")
        if len(set(self.accepted_symbols)) != len(self.accepted_symbols):
            raise ValueError("accepted_symbols must be unique")
        return self


class ExnessEvidencePeriodsConfig(StrictModel):
    development_start: date
    development_end: date
    replication_start: date
    replication_end: date
    forward_start: date
    forward_end: date

    @model_validator(mode="after")
    def validate_periods(self) -> ExnessEvidencePeriodsConfig:
        intervals = (
            (self.development_start, self.development_end),
            (self.replication_start, self.replication_end),
            (self.forward_start, self.forward_end),
        )
        if any(start >= end for start, end in intervals):
            raise ValueError("Each Phase-9 evidence period must have positive length")
        if self.development_end != self.replication_start:
            raise ValueError("development_end must equal replication_start")
        if self.replication_end != self.forward_start:
            raise ValueError("replication_end must equal forward_start")
        return self


class ExnessQuoteFeaturesConfig(StrictModel):
    observation_minutes: int = Field(gt=0)
    activity_baseline_minutes: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_windows(self) -> ExnessQuoteFeaturesConfig:
        if self.observation_minutes % 5 or self.activity_baseline_minutes % 5:
            raise ValueError("Phase-9 feature windows must be M5-aligned")
        if self.activity_baseline_minutes <= self.observation_minutes:
            raise ValueError("activity baseline must exceed observation window")
        return self


class ExnessQuoteModelConfig(StrictModel):
    l2_penalty: float = Field(gt=0)
    decision_threshold: float = Field(gt=0, lt=1)
    variants: tuple[ExnessModelVariant, ...]

    @model_validator(mode="after")
    def validate_variants(self) -> ExnessQuoteModelConfig:
        required = {"price_only", "activity_only", "price_activity"}
        if set(self.variants) != required or len(self.variants) != len(required):
            raise ValueError("Phase-9 model variants must match the frozen set")
        return self


class ExnessQuoteExecutionConfig(StrictModel):
    stop_buffer_pips: float = Field(gt=0)
    minimum_risk_pips: float = Field(gt=0)
    maximum_risk_pips: float = Field(gt=0)
    target_r_multiple: float = Field(gt=0)
    slippage_per_side_pips: float = Field(ge=0)
    commission_usd_per_lot_per_side: float = Field(ge=0)
    usd_per_pip_per_standard_lot: float = Field(gt=0)
    intrabar_priority: Literal["stop_first"]
    london_cutoff: Literal["new_york_open"]
    new_york_cutoff: Literal["fx_day_boundary"]

    @model_validator(mode="after")
    def validate_risk(self) -> ExnessQuoteExecutionConfig:
        if self.maximum_risk_pips <= self.minimum_risk_pips:
            raise ValueError("maximum_risk_pips must exceed minimum_risk_pips")
        return self

    @property
    def commission_pips_per_side(self) -> float:
        return (
            self.commission_usd_per_lot_per_side
            / self.usd_per_pip_per_standard_lot
        )


class ExnessQuoteTrailingConfig(StrictModel):
    break_even_trigger_r: float = Field(gt=0)
    swing_bars: int = Field(ge=1)
    buffer_pips: float = Field(ge=0)


class ExnessQuoteGateConfig(StrictModel):
    minimum_auc: float = Field(gt=0.5, le=1)
    minimum_auc_improvement: float = Field(gt=0)
    minimum_log_loss_improvement: float = Field(gt=0)
    minimum_trades_per_month: float = Field(gt=0)
    minimum_expectancy_r: float = Field(ge=0)
    minimum_profit_factor: float = Field(gt=1)
    minimum_expectancy_improvement_r: float = Field(gt=0)
    require_positive_cluster_ci_lower: bool
    bootstrap_resamples: int = Field(ge=100)
    confidence_level: float = Field(gt=0, lt=1)
    random_seed: int = Field(ge=0)


class ExnessQuoteActivityConfig(StrictModel):
    data: ExnessQuoteDataConfig
    periods: ExnessEvidencePeriodsConfig
    features: ExnessQuoteFeaturesConfig
    model: ExnessQuoteModelConfig
    execution: ExnessQuoteExecutionConfig
    trailing: ExnessQuoteTrailingConfig
    gate: ExnessQuoteGateConfig


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


def load_opening_auction_config(path: Path) -> OpeningAuctionConfig:
    """Load and strictly validate the Phase-6 state-machine configuration."""

    return OpeningAuctionConfig.model_validate(_read_yaml(path))


def load_auction_taxonomy_config(path: Path) -> AuctionTaxonomyConfig:
    """Load and strictly validate the Phase-7 taxonomy configuration."""

    return AuctionTaxonomyConfig.model_validate(_read_yaml(path))


def load_balance_boundary_strategy_config(
    path: Path,
) -> BalanceBoundaryStrategyConfig:
    """Load and strictly validate the Phase-8 boundary-strategy configuration."""

    return BalanceBoundaryStrategyConfig.model_validate(_read_yaml(path))


def load_exness_quote_activity_config(path: Path) -> ExnessQuoteActivityConfig:
    """Load and strictly validate the frozen Phase-9 configuration."""

    return ExnessQuoteActivityConfig.model_validate(_read_yaml(path))
