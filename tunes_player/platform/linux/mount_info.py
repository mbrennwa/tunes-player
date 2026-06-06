"""Detect whether paths live on network filesystem mounts (Linux)."""

from __future__ import annotations

import functools
from pathlib import Path

_NETWORK_FS_TYPES = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smb3",
        "fuse.sshfs",
    }
)


@functools.lru_cache(maxsize=1)
def _mount_entries() -> tuple[tuple[Path, str], ...]:
    entries: list[tuple[Path, str]] = []
    try:
        text = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mountpoint = parts[1].replace("\\040", " ")
        fstype = parts[2]
        try:
            entries.append((Path(mountpoint).resolve(), fstype))
        except OSError:
            continue
    entries.sort(key=lambda item: len(str(item[0])), reverse=True)
    return tuple(entries)


def _path_under_mount(path: Path, mount: Path) -> bool:
    try:
        path.relative_to(mount)
        return True
    except ValueError:
        return path == mount


def _prefer_fstype(candidates: list[str]) -> str:
    """Pick the most meaningful fstype when several mounts share a mountpoint."""
    for fstype in candidates:
        if fstype in _NETWORK_FS_TYPES:
            return fstype
    for fstype in candidates:
        if fstype != "autofs":
            return fstype
    return candidates[0]


def filesystem_type_for_path(path: Path) -> str | None:
    """Return the mount fstype for *path*, or None if unknown."""
    try:
        resolved = path.resolve()
    except OSError:
        return None
    best_len = -1
    candidates: list[str] = []
    for mountpoint, fstype in _mount_entries():
        if not _path_under_mount(resolved, mountpoint):
            continue
        mount_len = len(str(mountpoint))
        if mount_len > best_len:
            best_len = mount_len
            candidates = [fstype]
        elif mount_len == best_len:
            candidates.append(fstype)
    if not candidates:
        return None
    return _prefer_fstype(candidates)


def is_network_mount_path(path: Path | str) -> bool:
    """True when *path* resides on NFS, CIFS, or similar remote filesystem."""
    fstype = filesystem_type_for_path(Path(path))
    return fstype is not None and fstype in _NETWORK_FS_TYPES


def clear_mount_cache() -> None:
    """Invalidate cached /proc/mounts parse (for tests)."""
    _mount_entries.cache_clear()
