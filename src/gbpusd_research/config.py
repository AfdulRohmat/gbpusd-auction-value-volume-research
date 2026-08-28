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
        return self


class ProjectConfig(StrictModel):
    research: ResearchConfig
    sessions: SessionsConfig


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
