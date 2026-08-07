"""Tunes — cross-platform music player (Linux/GTK first)."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _version_from_pyproject() -> str | None:
    """Read ``[project].version`` from the source-tree pyproject when present."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        import tomllib
    except ImportError:  # pragma: no cover — Python < 3.11
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except OSError:
        return None
    value = data.get("project", {}).get("version")
    return str(value) if value else None


def _resolve_version() -> str:
    # Prefer pyproject.toml (canonical per docs/RELEASE.md) so editable
    # checkouts stay correct even when dist-info is stale.
    from_pyproject = _version_from_pyproject()
    if from_pyproject:
        return from_pyproject
    try:
        return version("tunes-player")
    except PackageNotFoundError:
        return "0.0.0"


__version__ = _resolve_version()
