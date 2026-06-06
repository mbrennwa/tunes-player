"""Stage network-library files on local disk before mpv opens them."""

from __future__ import annotations

import hashlib
import logging
import shutil
import time
from pathlib import Path

LOG = logging.getLogger(__name__)

_MAX_CACHE_BYTES = 2 * 1024 * 1024 * 1024


def _cache_key(path: Path, *, size: int, mtime_ns: int) -> str:
    digest = hashlib.sha256(
        f"{path}:{size}:{mtime_ns}".encode("utf-8", errors="replace")
    ).hexdigest()
    return digest[:20]


def _is_network_library_path(path: Path) -> bool:
    try:
        from tunes_player.platform.linux.mount_info import is_network_mount_path

        return is_network_mount_path(path)
    except ImportError:
        return False


def _cache_entry_paths(cache_dir: Path) -> list[tuple[Path, int, float]]:
    entries: list[tuple[Path, int, float]] = []
    if not cache_dir.is_dir():
        return entries
    for child in cache_dir.iterdir():
        if not child.is_dir():
            continue
        total = 0
        for file_path in child.rglob("*"):
            if file_path.is_file():
                try:
                    total += file_path.stat().st_size
                except OSError:
                    continue
        try:
            accessed = child.stat().st_mtime
        except OSError:
            accessed = 0.0
        entries.append((child, total, accessed))
    return entries


def _evict_cache_if_needed(cache_dir: Path, *, needed_bytes: int) -> None:
    entries = _cache_entry_paths(cache_dir)
    total = sum(size for _, size, _ in entries)
    if total + needed_bytes <= _MAX_CACHE_BYTES:
        return
    entries.sort(key=lambda item: item[2])
    for entry_dir, size, _ in entries:
        if total + needed_bytes <= _MAX_CACHE_BYTES:
            break
        shutil.rmtree(entry_dir, ignore_errors=True)
        total -= size
        LOG.info("Evicted playback cache entry %s", entry_dir.name)


def stage_network_file_if_needed(path: str | Path, *, cache_dir: Path) -> str:
    """Return a local staged copy for network-library files, else the original path."""
    source = Path(path)
    if not source.is_file():
        return str(path)
    try:
        resolved = source.resolve()
    except OSError:
        return str(path)
    if not _is_network_library_path(resolved):
        return str(path)

    try:
        stat = resolved.stat()
    except OSError as exc:
        LOG.warning("Could not stat network library file %s: %s", resolved, exc)
        return str(path)

    key = _cache_key(resolved, size=stat.st_size, mtime_ns=stat.st_mtime_ns)
    entry_dir = cache_dir / key
    staged = entry_dir / resolved.name

    if staged.is_file():
        try:
            staged_stat = staged.stat()
        except OSError:
            staged_stat = None
        if staged_stat is not None and staged_stat.st_size == stat.st_size:
            entry_dir.touch(exist_ok=True)
            LOG.info("Using staged playback copy: %s", staged)
            return str(staged)

    cache_dir.mkdir(parents=True, exist_ok=True)
    _evict_cache_if_needed(cache_dir, needed_bytes=stat.st_size)
    entry_dir.mkdir(parents=True, exist_ok=True)
    temp = entry_dir / f".{resolved.name}.partial"
    started = time.monotonic()
    LOG.info("Staging network file for playback: %s", resolved)
    try:
        from tunes_player.platform.linux.io_priority import apply_idle_io_priority
    except ImportError:
        pass
    else:
        apply_idle_io_priority()
    try:
        shutil.copyfile(resolved, temp)
        temp.replace(staged)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        LOG.warning("Playback staging failed for %s: %s", resolved, exc)
        return str(path)
    elapsed = time.monotonic() - started
    LOG.info(
        "Staged %.1f MiB in %.2fs: %s",
        stat.st_size / (1024 * 1024),
        elapsed,
        staged,
    )
    return str(staged)
