"""Command-line entry point for reproducible research workflows."""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import httpx
from pydantic import ValidationError

from gbpusd_research.config import load_project_config
from gbpusd_research.data.histdata import (
    HistDataError,
    download_month,
    write_month_manifest,
)
from gbpusd_research.data.pipeline import build_day, tag_day_sessions
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

    parser.error(f"Unsupported command: {args.command}")
    return 2
