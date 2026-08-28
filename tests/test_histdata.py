import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from gbpusd_research.data.histdata import (
    HistDataError,
    archive_name,
    download_page_url,
    read_archive_for_utc_day,
)


def write_archive(path: Path, rows: str) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("DAT_ASCII_GBPUSD_T_202401.csv", rows)


def test_archive_naming_and_page_url() -> None:
    assert archive_name("gbpusd", 2024, 1) == ("HISTDATA_COM_ASCII_GBPUSD_T202401.zip")
    assert download_page_url("GBPUSD", 2024, 1).endswith(
        "?/ascii/tick-data-quotes/gbpusd/2024/1"
    )


def test_parse_fixed_est_timestamp_and_filter_utc_day(tmp_path: Path) -> None:
    path = tmp_path / archive_name("GBPUSD", 2024, 1)
    write_archive(
        path,
        "\n".join(
            (
                "20240101 185959999,1.27000,1.27020,0",
                "20240101 190000000,1.27010,1.27030,0",
                "20240102 185959999,1.27100,1.27120,0",
                "20240102 190000000,1.27110,1.27130,0",
            )
        ),
    )

    ticks = read_archive_for_utc_day(
        path, date(2024, 1, 2), pip_size=0.0001, chunksize=2
    )

    assert len(ticks) == 2
    assert ticks.iloc[0]["timestamp"] == datetime(2024, 1, 2, tzinfo=UTC)
    assert ticks.iloc[-1]["timestamp"] == datetime(
        2024, 1, 2, 23, 59, 59, 999000, tzinfo=UTC
    )
    assert ticks.iloc[0]["spread_pips"] == pytest.approx(2.0)
    assert ticks["activity"].sum() == 2


def test_reject_archive_without_exactly_one_csv(tmp_path: Path) -> None:
    path = tmp_path / "invalid.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("notes.txt", "not ticks")

    with pytest.raises(HistDataError, match="exactly one CSV"):
        read_archive_for_utc_day(path, date(2024, 1, 2), pip_size=0.0001)
