"""Streaming adapter for Exness Personal Area and MT5 tick exports.

The canonical activity unit is one Bid/Ask quote update. It is deliberately
named quote activity: neither the Exness archive nor OTC GBPUSD establishes a
centralized traded-volume measure.
"""

from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
import pandas as pd

from gbpusd_research.config import ExnessQuoteActivityConfig
from gbpusd_research.data.resample import resample_ticks_m5
from gbpusd_research.data.validation import validate_m5, validate_ticks
from gbpusd_research.utils.paths import resolve_within_project

SUPPORTED_SUFFIXES = {".csv", ".zip"}
MT5_COLUMNS = (
    "source",
    "symbol",
    "time_msc",
    "bid",
    "ask",
    "last",
    "volume",
    "volume_real",
    "flags",
)
EXNESS_COLUMNS = ("source", "symbol", "timestamp", "bid", "ask")


class ExnessDataError(ValueError):
    """Raised when an Exness/MT5 file violates the frozen data contract."""


def discover_tick_sources(input_path: Path) -> list[Path]:
    """Return deterministic CSV/ZIP source paths below a file or directory."""

    if input_path.is_file():
        if input_path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ExnessDataError(f"Unsupported tick file: {input_path.name}")
        return [input_path]
    if not input_path.is_dir():
        raise ExnessDataError(f"Exness input path does not exist: {input_path}")
    sources = sorted(
        path
        for path in input_path.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )
    if not sources:
        raise ExnessDataError(f"No CSV or ZIP tick files found below {input_path}")
    return sources


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _csv_members(path: Path) -> tuple[str, ...]:
    if path.suffix.lower() == ".csv":
        return (path.name,)
    try:
        with zipfile.ZipFile(path) as archive:
            corrupt = archive.testzip()
            if corrupt:
                raise ExnessDataError(
                    f"Corrupt ZIP member {corrupt!r} in {path.name}"
                )
            members = tuple(
                sorted(
                    member
                    for member in archive.namelist()
                    if not member.endswith("/") and member.lower().endswith(".csv")
                )
            )
    except zipfile.BadZipFile as exc:
        raise ExnessDataError(f"Invalid ZIP archive: {path.name}") from exc
    if not members:
        raise ExnessDataError(f"No CSV members in ZIP archive: {path.name}")
    return members


def inspect_tick_sources(paths: Sequence[Path]) -> dict[str, Any]:
    """Inspect file integrity and headers without loading full tick histories."""

    if not paths:
        raise ExnessDataError("At least one Exness tick source is required")
    files = []
    for path in paths:
        if not path.is_file():
            raise ExnessDataError(f"Tick source does not exist: {path}")
        members = _csv_members(path)
        member_headers = []
        for member in members:
            with _open_member(path, member) as stream:
                line = stream.readline().decode("utf-8-sig", errors="strict")
            member_headers.append(
                {"member": member, "first_row": next(csv.reader([line]), [])}
            )
        files.append(
            {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
                "members": member_headers,
            }
        )
    return {"source_files": files}


class _MemberContext:
    """Context manager that keeps a ZIP open while a member is consumed."""

    def __init__(self, path: Path, member: str) -> None:
        self.path = path
        self.member = member
        self.archive: zipfile.ZipFile | None = None
        self.stream: BinaryIO | None = None

    def __enter__(self) -> BinaryIO:
        if self.path.suffix.lower() == ".csv":
            self.stream = self.path.open("rb")
        else:
            self.archive = zipfile.ZipFile(self.path)
            self.stream = self.archive.open(self.member)
        return self.stream

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.stream is not None:
            self.stream.close()
        if self.archive is not None:
            self.archive.close()


def _open_member(path: Path, member: str) -> _MemberContext:
    return _MemberContext(path, member)


def _normalized_header(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        value.strip().strip('"').lower().replace(" ", "_") for value in values
    )


def _reader_contract(first_line: str) -> tuple[int | None, tuple[str, ...] | None]:
    values = next(csv.reader([first_line]), [])
    normalized = _normalized_header(values)
    has_named_price_columns = {"bid", "ask"}.issubset(normalized)
    has_named_time = bool({"timestamp", "time_msc"}.intersection(normalized))
    if has_named_price_columns and has_named_time:
        return 0, None
    if len(values) == len(EXNESS_COLUMNS):
        return None, EXNESS_COLUMNS
    if len(values) == len(MT5_COLUMNS):
        return None, MT5_COLUMNS
    raise ExnessDataError(
        "Unrecognized tick CSV layout; expected Exness 5-column or MT5 "
        f"9-column rows, found {len(values)} columns"
    )


def _column_lookup(columns: Sequence[object]) -> dict[str, object]:
    return {
        str(column).strip().strip('"').lower().replace(" ", "_"): column
        for column in columns
    }


def _parse_timestamp(frame: pd.DataFrame, lookup: dict[str, object]) -> pd.Series:
    if "time_msc" in lookup:
        numeric = pd.to_numeric(frame[lookup["time_msc"]], errors="raise")
        return pd.to_datetime(numeric, unit="ms", utc=True, errors="raise")
    if "timestamp" not in lookup:
        raise ExnessDataError("Tick CSV is missing timestamp/time_msc")
    raw = frame[lookup["timestamp"]].astype("string").str.strip()
    try:
        return pd.to_datetime(raw, format="mixed", utc=True, errors="raise")
    except (TypeError, ValueError) as exc:
        raise ExnessDataError("Unable to parse tick timestamps as UTC") from exc


def _normalize_chunk(
    raw: pd.DataFrame,
    *,
    accepted_symbols: frozenset[str],
    pip_size: float,
    source_name: str,
    previous_quote: tuple[float, float, float] | None,
) -> tuple[pd.DataFrame, tuple[float, float, float] | None]:
    lookup = _column_lookup(raw.columns)
    missing = {"bid", "ask"}.difference(lookup)
    if missing:
        raise ExnessDataError("Tick CSV missing column(s): " + ", ".join(missing))

    timestamp = _parse_timestamp(raw, lookup)
    bid = pd.to_numeric(raw[lookup["bid"]], errors="raise").astype("float64")
    ask = pd.to_numeric(raw[lookup["ask"]], errors="raise").astype("float64")
    if "symbol" in lookup:
        symbols = raw[lookup["symbol"]].astype("string").str.strip()
    else:
        symbols = pd.Series("GBPUSD", index=raw.index, dtype="string")
    unknown = sorted(set(symbols.dropna().unique()).difference(accepted_symbols))
    if unknown:
        raise ExnessDataError(
            "Unexpected symbol(s) in tick source: " + ", ".join(unknown)
        )

    content: dict[str, Any] = {
        "timestamp": timestamp,
        "symbol": symbols,
        "bid": bid,
        "ask": ask,
    }
    for optional in ("flags", "last", "volume", "volume_real"):
        if optional in lookup:
            content[optional] = pd.to_numeric(raw[lookup[optional]], errors="raise")
    output = pd.DataFrame(content)
    output = output.dropna(subset=["timestamp", "symbol", "bid", "ask"])
    output = output.drop_duplicates(subset=["timestamp", "bid", "ask"])
    output = output.reset_index(drop=True)
    if output.empty:
        return output, previous_quote
    if (output[["bid", "ask"]] <= 0).any(axis=None):
        raise ExnessDataError(f"Non-positive quote in {source_name}")
    if not output["timestamp"].is_monotonic_increasing:
        raise ExnessDataError(f"Tick timestamps are not sorted in {source_name}")

    output["mid"] = (output["bid"] + output["ask"]) / 2
    output["spread_pips"] = (output["ask"] - output["bid"]) / pip_size
    output["activity"] = np.int8(1)
    prior = output["mid"].shift(1)
    if previous_quote is not None:
        prior.iloc[0] = previous_quote[2]
    change = output["mid"] - prior
    output["mid_direction"] = np.sign(change.fillna(0)).astype("int8")

    prior_bid = output["bid"].shift(1)
    prior_ask = output["ask"].shift(1)
    if previous_quote is not None:
        prior_bid.iloc[0] = previous_quote[0]
        prior_ask.iloc[0] = previous_quote[1]
    output["bid_changed"] = output["bid"].ne(prior_bid).astype("int8")
    output["ask_changed"] = output["ask"].ne(prior_ask).astype("int8")
    output["source_archive"] = source_name

    last = output.iloc[-1]
    return output, (float(last["bid"]), float(last["ask"]), float(last["mid"]))


def iter_exness_ticks(
    path: Path,
    *,
    accepted_symbols: Sequence[str],
    pip_size: float,
    chunksize: int = 500_000,
) -> Iterator[pd.DataFrame]:
    """Yield canonical UTC quote chunks from one CSV or ZIP source."""

    if chunksize < 1:
        raise ValueError("chunksize must be positive")
    accepted = frozenset(accepted_symbols)
    previous_quote: tuple[float, float, float] | None = None
    for member in _csv_members(path):
        with _open_member(path, member) as probe:
            first_line = probe.readline().decode("utf-8-sig", errors="strict")
        header, names = _reader_contract(first_line)
        with _open_member(path, member) as stream:
            reader = pd.read_csv(
                stream,
                header=header,
                names=names,
                chunksize=chunksize,
                encoding="utf-8-sig",
                low_memory=False,
            )
            for raw in reader:
                normalized, previous_quote = _normalize_chunk(
                    raw,
                    accepted_symbols=accepted,
                    pip_size=pip_size,
                    source_name=f"{path.name}:{member}",
                    previous_quote=previous_quote,
                )
                if not normalized.empty:
                    yield normalized


def _stream_m5(
    paths: Sequence[Path],
    *,
    accepted_symbols: Sequence[str],
    pip_size: float,
    chunksize: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    bars: list[pd.DataFrame] = []
    carry = pd.DataFrame()
    last_timestamp: pd.Timestamp | None = None
    raw_rows = 0
    crossed_quotes = 0
    max_spread = float("-inf")
    minimum_timestamp: pd.Timestamp | None = None

    for path in paths:
        for chunk in iter_exness_ticks(
            path,
            accepted_symbols=accepted_symbols,
            pip_size=pip_size,
            chunksize=chunksize,
        ):
            first_chunk_timestamp = chunk.iloc[0]["timestamp"]
            if (
                last_timestamp is not None
                and first_chunk_timestamp < last_timestamp
            ):
                raise ExnessDataError(
                    "Tick sources overlap or are out of chronological order near "
                    f"{chunk.iloc[0]['timestamp']}"
                )
            last_timestamp = chunk.iloc[-1]["timestamp"]
            minimum_timestamp = minimum_timestamp or chunk.iloc[0]["timestamp"]
            raw_rows += len(chunk)
            crossed_quotes += int(chunk["ask"].lt(chunk["bid"]).sum())
            max_spread = max(max_spread, float(chunk["spread_pips"].max()))

            combined = pd.concat([carry, chunk], ignore_index=True)
            bucket = combined["timestamp"].dt.floor("5min")
            last_bucket = bucket.iloc[-1]
            ready = combined[bucket.lt(last_bucket)]
            carry = combined[bucket.eq(last_bucket)].copy()
            if not ready.empty:
                bars.append(resample_ticks_m5(ready))

    if not carry.empty:
        bars.append(resample_ticks_m5(carry))
    if not bars:
        raise ExnessDataError("No ticks were decoded from the supplied sources")
    output = pd.concat(bars, ignore_index=True)
    if output["timestamp"].duplicated().any():
        raise ExnessDataError("Duplicate M5 buckets produced by input archives")
    quality = {
        "raw_quote_rows": raw_rows,
        "first_timestamp": minimum_timestamp.isoformat()
        if minimum_timestamp is not None
        else None,
        "last_timestamp": last_timestamp.isoformat()
        if last_timestamp is not None
        else None,
        "crossed_quote_rows": crossed_quotes,
        "max_spread_pips": max_spread,
    }
    return output, quality


def _atomic_parquet(frame: pd.DataFrame, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    frame.to_parquet(temporary, index=False)
    temporary.replace(destination)


def _atomic_json(content: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_text(
        json.dumps(content, indent=2, sort_keys=True), encoding="utf-8"
    )
    temporary.replace(destination)


def build_exness_m5(
    project_root: Path,
    config: ExnessQuoteActivityConfig,
    paths: Sequence[Path],
    *,
    chunksize: int = 500_000,
    force: bool = False,
) -> dict[str, Any]:
    """Stream Exness/MT5 ticks into compact monthly M5 Parquet partitions."""

    if not paths:
        raise ExnessDataError("At least one Exness tick source is required")
    metadata = inspect_tick_sources(paths)
    fingerprint = hashlib.sha256(
        "".join(
            str(item["sha256"])
            for item in metadata["source_files"]
        ).encode()
    ).hexdigest()
    processed_root = resolve_within_project(project_root, config.data.processed_path)
    manifest_path = processed_root / "manifests" / f"build-{fingerprint}.json"
    if manifest_path.is_file() and not force:
        cached = json.loads(manifest_path.read_text(encoding="utf-8"))
        cached["status"] = "cached"
        return cached

    bars, stream_quality = _stream_m5(
        paths,
        accepted_symbols=config.data.accepted_symbols,
        pip_size=config.data.pip_size,
        chunksize=chunksize,
    )
    tick_proxy = pd.DataFrame(
        {
            "timestamp": bars["timestamp"],
            "bid": bars["bid_close"],
            "ask": bars["ask_close"],
            "mid": bars["mid_close"],
            "spread_pips": bars["spread_median_pips"],
            "activity": bars["activity_count"],
        }
    )
    tick_quality = validate_ticks(tick_proxy, max_spread_pips=10_000)
    m5_quality = validate_m5(bars)
    if stream_quality["crossed_quote_rows"]:
        raise ExnessDataError(f"Crossed quotes found: {stream_quality}")
    if not tick_quality["valid"] or not m5_quality["valid"]:
        raise ExnessDataError(
            f"Exness M5 quality validation failed: {tick_quality}, {m5_quality}"
        )

    outputs = []
    years = bars["timestamp"].dt.year
    months = bars["timestamp"].dt.month
    for (year, month), frame in bars.groupby([years, months], sort=True):
        destination = (
            processed_root
            / "m5_monthly"
            / "symbol=GBPUSD"
            / f"year={year:04d}"
            / f"m5-{year:04d}-{month:02d}.parquet"
        )
        _atomic_parquet(frame.reset_index(drop=True), destination)
        outputs.append(str(destination.relative_to(project_root)))

    summary = {
        "status": "built",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_kind": config.data.source_preference,
        "source_fingerprint": fingerprint,
        "sources": metadata["source_files"],
        "stream_quality": stream_quality,
        "tick_quality": tick_quality,
        "m5_quality": m5_quality,
        "monthly_outputs": outputs,
    }
    _atomic_json(summary, manifest_path)
    summary["manifest"] = str(manifest_path.relative_to(project_root))
    return summary
