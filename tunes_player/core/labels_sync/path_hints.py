"""Heuristics for whether a labels sync path looks like a known sync layout."""

from __future__ import annotations

from pathlib import Path

# Case-insensitive path-component / substring markers for common sync clients.
_PATH_MARKERS: tuple[str, ...] = (
    "nextcloud",
    "owncloud",
    "dropbox",
    "google drive",
    "googledrive",
    "onedrive",
    "icloud",
    "syncthing",
    "resilio",
    "mega",
    "pcloud",
    "seafile",
    "rclone",
)

# Marker names present in the folder or an ancestor (files or directories).
_ANCESTOR_MARKERS: tuple[str, ...] = (
    ".stfolder",
    ".dropbox",
    ".csync_journal.db",
    ".owncloudsync.log",
    ".nextcloud",
)

_UNRECOGNIZED_ADVISORY = (
    "Not a recognized sync folder — make sure another tool syncs it."
)


def unrecognized_sync_folder_advisory() -> str:
    """Status-row copy when heuristics do not recognize the sync folder."""
    return _UNRECOGNIZED_ADVISORY


def looks_like_known_sync_folder(path: str | Path) -> bool:
    """Return True if *path* looks like a layout Tunes associates with sync tools.

    Advisory only — false negatives are expected (e.g. Syncthing on an arbitrary
    local directory with no ``.stfolder`` visible to this process).
    """
    raw = str(path).strip() if path is not None else ""
    if not raw:
        return False

    expanded = Path(raw).expanduser()
    haystack = str(expanded).casefold()
    for marker in _PATH_MARKERS:
        if marker in haystack:
            return True

    try:
        current = expanded.resolve(strict=False)
    except OSError:
        current = expanded

    for ancestor in (current, *current.parents):
        for name in _ANCESTOR_MARKERS:
            candidate = ancestor / name
            try:
                if candidate.exists():
                    return True
            except OSError:
                break
        if ancestor.parent == ancestor:
            break

    return False
