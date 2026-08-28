import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

from gbpusd_research.config import load_project_config
from gbpusd_research.data.histdata import archive_path
from gbpusd_research.data.pipeline import build_day, tag_day_sessions

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_one_day_vertical_slice(tmp_path: Path) -> None:
    config = load_project_config(
        PROJECT_ROOT / "config/research.yaml",
        PROJECT_ROOT / "config/sessions.yaml",
    )
    day = date(2024, 1, 2)
    rows = "\n".join(
        (
            "20240102 030000000,1.27345,1.27355,0",
            "20240102 030459999,1.27350,1.27365,0",
            "20240102 030500000,1.27360,1.27375,0",
        )
    )
    source = archive_path(tmp_path / "data/raw/histdata", "GBPUSD", 2024, 1)
    source.parent.mkdir(parents=True)
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("DAT_ASCII_GBPUSD_T_202401.csv", rows)

    summary = build_day(tmp_path, config, day)

    assert summary["tick_quality"]["valid"] is True
    assert summary["m5_quality"]["valid"] is True
    ticks = pd.read_parquet(tmp_path / summary["tick_output"])
    bars = pd.read_parquet(tmp_path / summary["m5_output"])
    assert len(ticks) == 3
    assert len(bars) == 2
    assert (tmp_path / summary["quality_output"]).is_file()

    session_summary = tag_day_sessions(tmp_path, config, day)
    assert session_summary["session_events"] == 2
    assert session_summary["tagged_bars"] == 2
    assert (tmp_path / session_summary["calendar_output"]).is_file()
