import zipfile
from pathlib import Path

import pandas as pd
import pytest

from gbpusd_research.config import load_exness_quote_activity_config
from gbpusd_research.data.exness import (
    ExnessDataError,
    build_exness_m5,
    discover_tick_sources,
    inspect_tick_sources,
    iter_exness_ticks,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_zip(path: Path, member: str, content: str) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, content)


def test_parse_exness_headerless_archive_across_chunks(tmp_path: Path) -> None:
    path = tmp_path / "GBPUSD_2024.zip"
    write_zip(
        path,
        "GBPUSD.csv",
        "\n".join(
            (
                '"Exness","GBPUSD","2024-01-02 08:00:00.000","1.27000","1.27010"',
                '"Exness","GBPUSD","2024-01-02 08:00:01.000","1.27010","1.27020"',
                '"Exness","GBPUSD","2024-01-02 08:05:00.000","1.27005","1.27015"',
            )
        ),
    )

    chunks = list(
        iter_exness_ticks(
            path,
            accepted_symbols=("GBPUSD", "GBPUSD-r"),
            pip_size=0.0001,
            chunksize=2,
        )
    )
    ticks = pd.concat(chunks, ignore_index=True)

    assert len(chunks) == 2
    assert len(ticks) == 3
    assert str(ticks["timestamp"].dt.tz) == "UTC"
    assert ticks["mid_direction"].tolist() == [0, 1, -1]
    assert ticks["bid_changed"].tolist() == [1, 1, 1]
    assert ticks["spread_pips"].tolist() == pytest.approx([1.0, 1.0, 1.0])


def test_parse_mt5_named_csv_and_retain_audit_fields(tmp_path: Path) -> None:
    path = tmp_path / "mt5.csv"
    path.write_text(
        "source,symbol,time_msc,bid,ask,last,volume,volume_real,flags\n"
        "Exness-MT5,GBPUSD-r,1704182400000,1.27000,1.27010,0,0,0,6\n",
        encoding="utf-8",
    )

    ticks = next(
        iter_exness_ticks(
            path,
            accepted_symbols=("GBPUSD", "GBPUSD-r"),
            pip_size=0.0001,
        )
    )

    assert ticks.loc[0, "timestamp"] == pd.Timestamp("2024-01-02 08:00", tz="UTC")
    assert ticks.loc[0, "symbol"] == "GBPUSD-r"
    assert ticks.loc[0, "flags"] == 6
    assert ticks.loc[0, "volume_real"] == 0


def test_reject_unknown_symbol(tmp_path: Path) -> None:
    path = tmp_path / "wrong.csv"
    path.write_text(
        "source,symbol,timestamp,bid,ask\n"
        "Exness,EURUSD,2024-01-02 08:00:00,1.09,1.0901\n",
        encoding="utf-8",
    )

    with pytest.raises(ExnessDataError, match="Unexpected symbol"):
        list(
            iter_exness_ticks(
                path,
                accepted_symbols=("GBPUSD", "GBPUSD-r"),
                pip_size=0.0001,
            )
        )


def test_discover_inspect_and_stream_build_monthly_m5(tmp_path: Path) -> None:
    raw = tmp_path / "data/raw/exness"
    raw.mkdir(parents=True)
    source = raw / "GBPUSD_2024.zip"
    write_zip(
        source,
        "ticks.csv",
        "\n".join(
            (
                "source,symbol,timestamp,bid,ask",
                "Exness,GBPUSD,2024-01-31 23:59:58,1.27000,1.27010",
                "Exness,GBPUSD,2024-01-31 23:59:59,1.27010,1.27020",
                "Exness,GBPUSD,2024-02-01 00:00:00,1.27020,1.27030",
                "Exness,GBPUSD,2024-02-01 00:05:00,1.27030,1.27040",
            )
        ),
    )
    sources = discover_tick_sources(raw)
    inspection = inspect_tick_sources(sources)

    assert sources == [source]
    assert len(inspection["source_files"][0]["sha256"]) == 64
    config = load_exness_quote_activity_config(
        PROJECT_ROOT / "config/exness_quote_activity.yaml"
    )
    summary = build_exness_m5(tmp_path, config, sources, chunksize=2)

    assert summary["status"] == "built"
    assert summary["stream_quality"]["raw_quote_rows"] == 4
    assert len(summary["monthly_outputs"]) == 2
    for relative in summary["monthly_outputs"]:
        frame = pd.read_parquet(tmp_path / relative)
        assert not frame.empty
        assert {
            "up_quote_count",
            "down_quote_count",
            "spread_median_pips",
        }.issubset(frame.columns)

    cached = build_exness_m5(tmp_path, config, sources, chunksize=2)
    assert cached["status"] == "cached"
