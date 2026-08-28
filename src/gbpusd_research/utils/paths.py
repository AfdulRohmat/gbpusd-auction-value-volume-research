"""Safe project-root and data-path helpers."""

from pathlib import Path


def find_project_root(start: Path) -> Path:
    """Find the nearest parent containing the project metadata file."""

    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise ValueError(f"Could not find pyproject.toml from {start}")


def resolve_within_project(project_root: Path, configured_path: Path) -> Path:
    """Resolve a relative path while preventing traversal outside the project."""

    root = project_root.resolve()
    resolved = (
        configured_path.resolve()
        if configured_path.is_absolute()
        else (root / configured_path).resolve()
    )
    if not resolved.is_relative_to(root):
        raise ValueError(f"Configured path escapes project root: {configured_path}")
    return resolved
