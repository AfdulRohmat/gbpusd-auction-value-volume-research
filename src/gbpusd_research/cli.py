"""Command-line entry point for reproducible research workflows."""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import httpx
from pydantic import ValidationError

from gbpusd_research.config import (
    load_fundamental_bias_config,
    load_fundamental_repricing_config,
    load_fundamental_strength_config,
    load_opening_value_strategy_config,
    load_project_config,
    load_value_state_config,
)
from gbpusd_research.data.histdata import (
    HistDataError,
    download_month,
    write_month_manifest,
)
from gbpusd_research.data.pipeline import (
    build_day,
    build_month_m5,
    iter_months,
    tag_day_sessions,
)
from gbpusd_research.research.phase1 import run_phase1
from gbpusd_research.research.phase2 import run_phase2
from gbpusd_research.research.phase3 import run_phase3
from gbpusd_research.research.phase3b import run_phase3b
from gbpusd_research.research.phase3c import run_phase3c
from gbpusd_research.research.phase4 import run_phase4
from gbpusd_research.utils.logging import configure_logging
from gbpusd_research.utils.paths import find_project_root, resolve_within_project


def _add_config_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--research",
        type=Path,
        default=Path("config/research.yaml"),
        help="research configuration path relative to the project root",
    )
    parser.add_argument(
        "--sessions",
        type=Path,
        default=Path("config/sessions.yaml"),
        help="session configuration path relative to the project root",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gbpusd-research")
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("config-check", help="validate configuration")
    _add_config_arguments(check)

    show = subparsers.add_parser("show-config", help="print normalized configuration")
    _add_config_arguments(show)

    download = subparsers.add_parser(
        "download", help="download/cache the HistData month containing a UTC date"
    )
    _add_config_arguments(download)
    download.add_argument("--date", type=date.fromisoformat, required=True)
    download.add_argument("--timeout-seconds", type=float, default=60)

    build = subparsers.add_parser(
        "build-m5", help="decode one cached UTC day and produce tick/M5 Parquet"
    )
    _add_config_arguments(build)
    build.add_argument("--date", type=date.fromisoformat, required=True)

    sessions = subparsers.add_parser(
        "tag-sessions", help="tag London/New York windows in one UTC M5 day"
    )
    _add_config_arguments(sessions)
    sessions.add_argument("--date", type=date.fromisoformat, required=True)

    download_range = subparsers.add_parser(
        "download-range", help="download every source month in the configured range"
    )
    _add_config_arguments(download_range)
    download_range.add_argument("--timeout-seconds", type=float, default=120)
    download_range.add_argument("--attempts", type=int, default=3)

    build_range = subparsers.add_parser(
        "build-range", help="build monthly M5 files for the configured range"
    )
    _add_config_arguments(build_range)
    build_range.add_argument("--force", action="store_true")

    phase1 = subparsers.add_parser(
        "run-phase1", help="create Phase-1 events, controls, statistics, and report"
    )
    _add_config_arguments(phase1)

    phase2 = subparsers.add_parser(
        "run-phase2", help="create point-in-time value-state features and report"
    )
    _add_config_arguments(phase2)
    phase2.add_argument(
        "--value-state",
        type=Path,
        default=Path("config/value_state.yaml"),
        help="Phase-2 value-state configuration relative to the project root",
    )

    phase3 = subparsers.add_parser(
        "run-phase3", help="create point-in-time policy-bias features and report"
    )
    _add_config_arguments(phase3)
    phase3.add_argument(
        "--value-state",
        type=Path,
        default=Path("config/value_state.yaml"),
        help="Phase-2 value-state configuration relative to the project root",
    )
    phase3.add_argument(
        "--fundamental",
        type=Path,
        default=Path("config/fundamental_bias.yaml"),
        help="Phase-3 fundamental configuration relative to the project root",
    )

    phase3b = subparsers.add_parser(
        "run-phase3b",
        help="create point-in-time relative fundamental-strength features and report",
    )
    _add_config_arguments(phase3b)
    phase3b.add_argument(
        "--value-state",
        type=Path,
        default=Path("config/value_state.yaml"),
        help="Phase-2 value-state configuration relative to the project root",
    )
    phase3b.add_argument(
        "--fundamental-strength",
        type=Path,
        default=Path("config/fundamental_strength.yaml"),
        help="Phase-3B strength configuration relative to the project root",
    )

    phase3c = subparsers.add_parser(
        "run-phase3c",
        help="test point-in-time event-day 2Y repricing at future session opens",
    )
    _add_config_arguments(phase3c)
    phase3c.add_argument(
        "--value-state",
        type=Path,
        default=Path("config/value_state.yaml"),
        help="Phase-2 value-state configuration relative to the project root",
    )
    phase3c.add_argument(
        "--fundamental-repricing",
        type=Path,
        default=Path("config/fundamental_repricing.yaml"),
        help="Phase-3C repricing configuration relative to the project root",
    )

    phase4 = subparsers.add_parser(
        "run-phase4",
        help="run frozen opening-value development and untouched validation",
    )
    _add_config_arguments(phase4)
    phase4.add_argument(
        "--validation-research",
        type=Path,
        default=Path("config/research_2025.yaml"),
        help="validation research configuration relative to the project root",
    )
    phase4.add_argument(
        "--value-state",
        type=Path,
        default=Path("config/value_state.yaml"),
        help="Phase-2 value-state configuration relative to the project root",
    )
    phase4.add_argument(
        "--opening-value",
        type=Path,
        default=Path("config/opening_value_strategy.yaml"),
        help="frozen Phase-4 strategy configuration relative to the project root",
    )
    return parser


def _resolve_config_path(project_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else project_root / path


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose)

    try:
        project_root = find_project_root(Path.cwd())
        config = load_project_config(
            _resolve_config_path(project_root, args.research),
            _resolve_config_path(project_root, args.sessions),
        )
    except (ValueError, ValidationError) as exc:
        logging.getLogger(__name__).error("Configuration error: %s", exc)
        return 2

    if args.command == "config-check":
        print("Configuration valid")
        return 0
    if args.command == "show-config":
        print(json.dumps(config.model_dump(mode="json"), indent=2, sort_keys=True))
        return 0
    if args.command == "download":
        research = config.research
        if not research.data.start <= args.date < research.data.end:
            logging.getLogger(__name__).error(
                "Requested date %s is outside configured half-open range [%s, %s)",
                args.date,
                research.data.start,
                research.data.end,
            )
            return 2
        raw_root = resolve_within_project(project_root, research.data.paths.raw)
        try:
            result = download_month(
                raw_root=raw_root,
                symbol=research.instrument.symbol,
                year=args.date.year,
                month=args.date.month,
                timeout_seconds=args.timeout_seconds,
            )
        except (HistDataError, httpx.HTTPError, OSError) as exc:
            logging.getLogger(__name__).error("Download failed: %s", exc)
            return 1
        manifest = write_month_manifest(
            raw_root,
            research.instrument.symbol,
            args.date.year,
            args.date.month,
            result,
        )
        print(
            json.dumps(
                {
                    "year_month": f"{args.date.year:04d}-{args.date.month:02d}",
                    "manifest": str(manifest.relative_to(project_root)),
                    **result,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "build-m5":
        try:
            summary = build_day(project_root, config, args.date)
        except ValueError as exc:
            logging.getLogger(__name__).error("Build failed: %s", exc)
            return 1
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    if args.command == "tag-sessions":
        try:
            summary = tag_day_sessions(project_root, config, args.date)
        except ValueError as exc:
            logging.getLogger(__name__).error("Session tagging failed: %s", exc)
            return 1
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    if args.command == "download-range":
        research = config.research
        raw_root = resolve_within_project(project_root, research.data.paths.raw)
        results = []
        failures = []
        for year, month in iter_months(research.data.start, research.data.end):
            logging.getLogger(__name__).info("Downloading %04d-%02d", year, month)
            for attempt in range(1, args.attempts + 1):
                try:
                    result = download_month(
                        raw_root=raw_root,
                        symbol=research.instrument.symbol,
                        year=year,
                        month=month,
                        timeout_seconds=args.timeout_seconds,
                    )
                    manifest = write_month_manifest(
                        raw_root, research.instrument.symbol, year, month, result
                    )
                    results.append(
                        {
                            "year_month": f"{year:04d}-{month:02d}",
                            "manifest": str(manifest.relative_to(project_root)),
                            **result,
                        }
                    )
                    break
                except (HistDataError, httpx.HTTPError, OSError) as exc:
                    logging.getLogger(__name__).warning(
                        "Attempt %d/%d failed for %04d-%02d: %s",
                        attempt,
                        args.attempts,
                        year,
                        month,
                        exc,
                    )
                    if attempt < args.attempts:
                        time.sleep(attempt)
            else:
                failures.append(f"{year:04d}-{month:02d}")
        print(
            json.dumps(
                {"months": results, "failed_months": failures},
                indent=2,
                sort_keys=True,
            )
        )
        return 1 if failures else 0
    if args.command == "build-range":
        research = config.research
        results = []
        for year, month in iter_months(research.data.start, research.data.end):
            logging.getLogger(__name__).info("Building M5 %04d-%02d", year, month)
            try:
                results.append(
                    build_month_m5(project_root, config, year, month, force=args.force)
                )
            except ValueError as exc:
                logging.getLogger(__name__).error(
                    "M5 build failed for %04d-%02d: %s", year, month, exc
                )
                return 1
        print(json.dumps({"months": results}, indent=2, sort_keys=True))
        return 0
    if args.command == "run-phase1":
        try:
            summary = run_phase1(project_root, config)
        except ValueError as exc:
            logging.getLogger(__name__).error("Phase-1 run failed: %s", exc)
            return 1
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    if args.command == "run-phase2":
        try:
            value_config = load_value_state_config(
                _resolve_config_path(project_root, args.value_state)
            )
            summary = run_phase2(project_root, config, value_config)
        except (ValueError, ValidationError) as exc:
            logging.getLogger(__name__).error("Phase-2 run failed: %s", exc)
            return 1
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    if args.command == "run-phase3":
        try:
            value_config = load_value_state_config(
                _resolve_config_path(project_root, args.value_state)
            )
            fundamental_config = load_fundamental_bias_config(
                _resolve_config_path(project_root, args.fundamental)
            )
            summary = run_phase3(project_root, config, value_config, fundamental_config)
        except (ValueError, ValidationError) as exc:
            logging.getLogger(__name__).error("Phase-3 run failed: %s", exc)
            return 1
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    if args.command == "run-phase3b":
        try:
            value_config = load_value_state_config(
                _resolve_config_path(project_root, args.value_state)
            )
            fundamental_config = load_fundamental_strength_config(
                _resolve_config_path(project_root, args.fundamental_strength)
            )
            summary = run_phase3b(
                project_root, config, value_config, fundamental_config
            )
        except (ValueError, ValidationError) as exc:
            logging.getLogger(__name__).error("Phase-3B run failed: %s", exc)
            return 1
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    if args.command == "run-phase3c":
        try:
            value_config = load_value_state_config(
                _resolve_config_path(project_root, args.value_state)
            )
            repricing_config = load_fundamental_repricing_config(
                _resolve_config_path(project_root, args.fundamental_repricing)
            )
            summary = run_phase3c(
                project_root, config, value_config, repricing_config
            )
        except (ValueError, ValidationError) as exc:
            logging.getLogger(__name__).error("Phase-3C run failed: %s", exc)
            return 1
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    if args.command == "run-phase4":
        try:
            validation_config = load_project_config(
                _resolve_config_path(project_root, args.validation_research),
                _resolve_config_path(project_root, args.sessions),
            )
            value_config = load_value_state_config(
                _resolve_config_path(project_root, args.value_state)
            )
            strategy_config = load_opening_value_strategy_config(
                _resolve_config_path(project_root, args.opening_value)
            )
            summary = run_phase4(
                project_root,
                config,
                validation_config,
                value_config,
                strategy_config,
            )
        except (ValueError, ValidationError) as exc:
            logging.getLogger(__name__).error("Phase-4 run failed: %s", exc)
            return 1
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2
