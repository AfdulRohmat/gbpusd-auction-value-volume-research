from pathlib import Path

import pytest

from gbpusd_research.utils.paths import find_project_root, resolve_within_project

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_find_project_root_from_nested_directory() -> None:
    assert find_project_root(PROJECT_ROOT / "src/gbpusd_research") == PROJECT_ROOT


def test_resolve_relative_path_within_project() -> None:
    assert resolve_within_project(PROJECT_ROOT, Path("data/raw")) == (
        PROJECT_ROOT / "data/raw"
    )


def test_reject_path_outside_project() -> None:
    with pytest.raises(ValueError, match="escapes project root"):
        resolve_within_project(PROJECT_ROOT, Path("../outside"))
