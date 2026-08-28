"""HistData Generic ASCII tick archive adapter.

HistData publishes one ZIP per symbol/month. Generic tick rows contain a fixed
EST (UTC-05:00, without daylight-saving adjustment) timestamp, bid, ask, and a
provider volume field that is normally zero. Tick count is therefore the V1
activity proxy.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

BASE_URL = "https://www.histdata.com"
SOURCE_TIMEZONE = "Etc/GMT+5"
TOKEN_PATTERN = re.compile(r'name="tk"[^>]+value="([^"]+)"')


class HistDataError(ValueError):
    """Raised when a HistData page or archive violates its contract."""


def archive_name(symbol: str, year: int, month: int) -> str:
    return f"HISTDATA_COM_ASCII_{symbol.upper()}_T{year:04d}{month:02d}.zip"


def archive_path(raw_root: Path, symbol: str, year: int, month: int) -> Path:
    return raw_root / symbol.upper() / f"{year:04d}" / archive_name(symbol, year, month)


def download_page_url(symbol: str, year: int, month: int) -> str:
    return (
        f"{BASE_URL}/download-free-forex-historical-data/"
        f"?/ascii/tick-data-quotes/{symbol.lower()}/{year:04d}/{month}"
    )


def _validate_archive(path: Path) -> tuple[str, int]:
    try:
        with zipfile.ZipFile(path) as archive:
            members = [
                name for name in archive.namelist() if name.lower().endswith(".csv")
            ]
            if len(members) != 1:
                raise HistDataError(
                    f"Expected exactly one CSV in archive, found {len(members)}"
                )
            corrupt = archive.testzip()
            if corrupt:
                raise HistDataError(f"Corrupt ZIP member: {corrupt}")
            return members[0], archive.getinfo(members[0]).file_size
    except zipfile.BadZipFile as exc:
        raise HistDataError("Downloaded file is not a valid ZIP archive") from exc


def download_month(
    *,
    raw_root: Path,
    symbol: str,
    year: int,
    month: int,
    timeout_seconds: float = 60,
) -> dict[str, Any]:
    """Download and atomically cache one free monthly Generic ASCII archive."""

    if month not in range(1, 13):
        raise ValueError("month must be between 1 and 12")
    destination = archive_path(raw_root, symbol, year, month)
    if destination.is_file():
        member, uncompressed_bytes = _validate_archive(destination)
        return _download_result(
            destination,
            raw_root,
            status="cached",
            csv_member=member,
            uncompressed_bytes=uncompressed_bytes,
        )

    page_url = download_page_url(symbol, year, month)
    headers = {"User-Agent": "gbpusd-session-research/0.1"}
    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 15))
    transport = httpx.HTTPTransport(retries=3)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".zip.part")

    try:
        with httpx.Client(
            headers=headers,
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        ) as client:
            page = client.get(page_url)
            page.raise_for_status()
            token_match = TOKEN_PATTERN.search(page.text)
            if not token_match:
                raise HistDataError("Security token not found on HistData page")
            form = {
                "tk": token_match.group(1),
                "date": f"{year:04d}",
                "datemonth": f"{year:04d}{month:02d}",
                "platform": "ASCII",
                "timeframe": "T",
                "fxpair": symbol.upper(),
            }
            with client.stream(
                "POST",
                f"{BASE_URL}/get.php",
                data=form,
                headers={"Referer": page_url},
            ) as response:
                response.raise_for_status()
                disposition = response.headers.get("content-disposition", "")
                if ".zip" not in disposition.lower():
                    raise HistDataError(
                        "HistData response is not a ZIP attachment: " + disposition
                    )
                with temporary.open("wb") as stream:
                    for chunk in response.iter_bytes():
                        stream.write(chunk)
        temporary.replace(destination)
        member, uncompressed_bytes = _validate_archive(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return _download_result(
        destination,
        raw_root,
        status="downloaded",
        csv_member=member,
        uncompressed_bytes=uncompressed_bytes,
    )


def _download_result(
    destination: Path,
    raw_root: Path,
    *,
    status: str,
    csv_member: str,
    uncompressed_bytes: int,
) -> dict[str, Any]:
    payload_hash = hashlib.sha256()
    with destination.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            payload_hash.update(chunk)
    return {
        "status": status,
        "relative_path": str(destination.relative_to(raw_root)),
        "byte_size": destination.stat().st_size,
        "sha256": payload_hash.hexdigest(),
        "csv_member": csv_member,
        "uncompressed_bytes": uncompressed_bytes,
    }


def write_month_manifest(
    raw_root: Path, symbol: str, year: int, month: int, result: dict[str, Any]
) -> Path:
    manifest_dir = raw_root / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    destination = manifest_dir / f"{symbol.upper()}-{year:04d}-{month:02d}.json"
    temporary = destination.with_suffix(".json.part")
    content = {
        "source": "histdata",
        "symbol": symbol.upper(),
        "year": year,
        "month": month,
        "recorded_at": datetime.now(UTC).isoformat(),
        **result,
    }
    temporary.write_text(
        json.dumps(content, indent=2, sort_keys=True), encoding="utf-8"
    )
    temporary.replace(destination)
    return destination


def read_archive_for_utc_day(
    path: Path,
    day: date,
    *,
    pip_size: float,
    chunksize: int = 500_000,
) -> pd.DataFrame:
    """Read ticks belonging to one UTC day from a monthly HistData archive."""

    member, _ = _validate_archive(path)
    frames: list[pd.DataFrame] = []
    with zipfile.ZipFile(path) as archive, archive.open(member) as stream:
        chunks = pd.read_csv(
            stream,
            header=None,
            names=["source_timestamp", "bid", "ask", "source_volume"],
            dtype={
                "source_timestamp": "string",
                "bid": "float64",
                "ask": "float64",
                "source_volume": "float32",
            },
            chunksize=chunksize,
        )
        for chunk in chunks:
            local = pd.to_datetime(
                chunk.pop("source_timestamp"),
                format="%Y%m%d %H%M%S%f",
                errors="raise",
            ).dt.tz_localize(SOURCE_TIMEZONE)
            chunk.insert(0, "timestamp", local.dt.tz_convert("UTC"))
            selected = chunk[chunk["timestamp"].dt.date == day].copy()
            if selected.empty:
                continue
            selected["mid"] = (selected["bid"] + selected["ask"]) / 2
            selected["spread_pips"] = (
                (selected["ask"] - selected["bid"]) / pip_size
            ).astype("float32")
            selected["activity"] = 1
            selected["source_archive"] = path.name
            frames.append(selected)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)[
        [
            "timestamp",
            "bid",
            "ask",
            "mid",
            "spread_pips",
            "activity",
            "source_volume",
            "source_archive",
        ]
    ]
