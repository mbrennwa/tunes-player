"""Stage network-library files on local disk for repeat playback."""

from __future__ import annotations

import hashlib
import logging
import shutil
import threading
import time
from pathlib import Path

LOG = logging.getLogger(__name__)

_MAX_CACHE_BYTES = 2 * 1024 * 1024 * 1024
_warmup_lock = threading.Lock()
_warmup_inflight: set[str] = set()


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


def _staged_path_if_cached(resolved: Path, *, cache_dir: Path) -> Path | None:
    try:
        stat = resolved.stat()
    except OSError:
        return None
    key = _cache_key(resolved, size=stat.st_size, mtime_ns=stat.st_mtime_ns)
    staged = cache_dir / key / resolved.name
    if not staged.is_file():
        return None
    try:
        staged_stat = staged.stat()
    except OSError:
        return None
    if staged_stat.st_size != stat.st_size:
        return None
    (cache_dir / key).touch(exist_ok=True)
    return staged


def resolve_playback_target(path: str | Path, *, cache_dir: Path) -> str:
    """Return a cached local copy when available, else the original path (no blocking copy)."""
    source = Path(path)
    if not source.is_file():
        return str(path)
    try:
        resolved = source.resolve()
    except OSError:
        return str(path)
    if not _is_network_library_path(resolved):
        return str(path)
    staged = _staged_path_if_cached(resolved, cache_dir=cache_dir)
    if staged is not None:
        LOG.info("Using staged playback copy: %s", staged)
        return str(staged)
    return str(path)


def _copy_network_file_to_cache(resolved: Path, *, cache_dir: Path) -> Path | None:
    try:
        stat = resolved.stat()
    except OSError as exc:
        LOG.warning("Could not stat network library file %s: %s", resolved, exc)
        return None

    key = _cache_key(resolved, size=stat.st_size, mtime_ns=stat.st_mtime_ns)
    entry_dir = cache_dir / key
    staged = entry_dir / resolved.name
    existing = _staged_path_if_cached(resolved, cache_dir=cache_dir)
    if existing is not None:
        return existing

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
        return None
    elapsed = time.monotonic() - started
    LOG.info(
        "Staged %.1f MiB in %.2fs: %s",
        stat.st_size / (1024 * 1024),
        elapsed,
        staged,
    )
    return staged


def warm_playback_cache(path: str | Path, *, cache_dir: Path) -> str:
    """Copy a network-library file to the local cache; return the path mpv should open."""
    source = Path(path)
    if not source.is_file():
        return str(path)
    try:
        resolved = source.resolve()
    except OSError:
        return str(path)
    if not _is_network_library_path(resolved):
        return str(path)
    staged = _copy_network_file_to_cache(resolved, cache_dir=cache_dir)
    if staged is not None:
        return str(staged)
    return str(path)


def _warmup_worker(path: str | Path, *, cache_dir: Path, inflight_key: str) -> None:
    try:
        warm_playback_cache(path, cache_dir=cache_dir)
    finally:
        with _warmup_lock:
            _warmup_inflight.discard(inflight_key)


def schedule_playback_cache_warmup(path: str | Path, *, cache_dir: Path) -> None:
    """Start a background cache copy for repeat playback; never blocks the caller."""
    source = Path(path)
    if not source.is_file():
        return
    try:
        resolved = source.resolve()
    except OSError:
        return
    if not _is_network_library_path(resolved):
        return
    if _staged_path_if_cached(resolved, cache_dir=cache_dir) is not None:
        return
    try:
        stat = resolved.stat()
    except OSError:
        return
    inflight_key = _cache_key(resolved, size=stat.st_size, mtime_ns=stat.st_mtime_ns)
    with _warmup_lock:
        if inflight_key in _warmup_inflight:
            return
        _warmup_inflight.add(inflight_key)
    threading.Thread(
        target=_warmup_worker,
        args=(path,),
        kwargs={"cache_dir": cache_dir, "inflight_key": inflight_key},
        name="tunes-playback-cache-warm",
        daemon=True,
    ).start()


def clear_warmup_state_for_tests() -> None:
    """Reset in-flight warmup tracking (tests only)."""
    with _warmup_lock:
        _warmup_inflight.clear()
