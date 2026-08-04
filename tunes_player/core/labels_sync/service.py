"""Coordinate pull/merge/push of release labels to a sync folder."""

from __future__ import annotations

import hashlib
import logging
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tunes_player.core.labels_sync.folder_store import FolderRemoteStore
from tunes_player.core.labels_sync.format import (
    SYNC_RELATIVE_PATH,
    dumps_label_map,
    loads_label_map,
)
from tunes_player.core.labels_sync.merge import LabelMap, merge_label_maps
from tunes_player.core.labels_sync.store_protocol import ConflictError

logger = logging.getLogger(__name__)

_DEBOUNCE_SEC = 1.0
_MAX_PUT_RETRIES = 3
_WATCH_SUPPRESS_SEC = 5.0


@dataclass(frozen=True, slots=True)
class LabelSyncStatus:
    enabled: bool
    folder: str | None
    syncing: bool
    last_success_at: float | None
    last_error: str | None
    pending_dirty: bool


@dataclass(frozen=True, slots=True)
class _SyncResult:
    ok: bool
    changed: bool


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LabelSyncService:
    """Merge local SQLite label state with a folder-backed JSON document."""

    def __init__(
        self,
        *,
        library_store: Any,
        get_enabled: Callable[[], bool],
        get_folder: Callable[[], str | None],
        set_status: Callable[[float | None, str | None], None] | None = None,
        device_id: str | None = None,
        on_applied: Callable[[], None] | None = None,
    ) -> None:
        self._store = library_store
        self._get_enabled = get_enabled
        self._get_folder = get_folder
        self._set_status = set_status
        self._device_id = device_id or socket.gethostname() or "unknown"
        self._on_applied = on_applied
        self._lock = threading.RLock()
        self._syncing = False
        self._last_success_at: float | None = None
        self._last_error: str | None = None
        self._debounce_timer: threading.Timer | None = None
        self._ignore_watch_until = 0.0
        self._last_remote_digest: str | None = None

    def seed_status(
        self,
        last_success_at: float | None,
        last_error: str | None,
    ) -> None:
        self._last_success_at = last_success_at
        self._last_error = last_error

    @property
    def device_id(self) -> str:
        return self._device_id

    def ignore_watch_events(self) -> bool:
        """True shortly after a local write so Gio/OwnCloud echo does not re-sync."""
        return time.monotonic() < self._ignore_watch_until

    def remote_digest_unchanged(self) -> bool:
        """True when the on-disk sync file matches the last successfully synced digest."""
        if self._last_remote_digest is None:
            return False
        current = self._read_remote_digest()
        return current is not None and current == self._last_remote_digest

    def status(self) -> LabelSyncStatus:
        folder = self._normalized_folder()
        pending = False
        try:
            pending = bool(self._store.has_dirty_label_sync_rows())
        except Exception:
            pending = False
        return LabelSyncStatus(
            enabled=bool(self._get_enabled()),
            folder=folder,
            syncing=self._syncing,
            last_success_at=self._last_success_at,
            last_error=self._last_error,
            pending_dirty=pending,
        )

    def schedule_sync(self) -> None:
        if not self._get_enabled() or self._normalized_folder() is None:
            return
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            timer = threading.Timer(_DEBOUNCE_SEC, self._debounced_run)
            timer.daemon = True
            self._debounce_timer = timer
            timer.start()

    def flush(self) -> None:
        """Run immediately if a debounced sync is pending or rows are dirty."""
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None
                pending_timer = True
            else:
                pending_timer = False
        if pending_timer or self._should_sync():
            self.sync_now()

    def sync_now(self) -> bool:
        """Pull remote, merge with local, push, apply. Returns True on success."""
        if not self._get_enabled():
            return False
        folder = self._normalized_folder()
        if folder is None:
            self._record_error("Labels sync folder is not set")
            return False
        # Fast path: OwnCloud/Gio often re-touches an unchanged file.
        try:
            dirty = bool(self._store.has_dirty_label_sync_rows())
        except Exception:
            dirty = True
        if not dirty and self.remote_digest_unchanged():
            return True
        with self._lock:
            if self._syncing:
                return False
            self._syncing = True
        try:
            remote_store = FolderRemoteStore(folder)
            result = self._sync_with_store(remote_store)
            if result.ok:
                if result.changed or self._last_success_at is None:
                    self._last_success_at = time.time()
                    self._last_error = None
                    self._persist_status()
                elif self._last_error is not None:
                    self._last_error = None
                    self._persist_status()
                if result.changed and self._on_applied is not None:
                    self._on_applied()
            return result.ok
        except Exception as exc:
            logger.exception("label sync failed")
            self._record_error(str(exc))
            return False
        finally:
            with self._lock:
                self._syncing = False

    def export_to(self, path: Path | str) -> None:
        target = Path(path)
        if target.is_dir():
            target = target / Path(SYNC_RELATIVE_PATH).name
        target.parent.mkdir(parents=True, exist_ok=True)
        label_map = self._store.export_label_sync_map()
        target.write_bytes(dumps_label_map(label_map))

    def import_from(self, path: Path | str) -> None:
        source = Path(path)
        if source.is_dir():
            candidate = source / SYNC_RELATIVE_PATH
            if not candidate.is_file():
                candidate = source / Path(SYNC_RELATIVE_PATH).name
            source = candidate
        data = source.read_bytes()
        remote = loads_label_map(data)
        local = self._store.export_label_sync_map()
        merged = merge_label_maps(local, remote)
        self._store.apply_label_sync_map(merged, clear_dirty=False)
        if self._on_applied is not None:
            self._on_applied()
        self.schedule_sync()

    def _should_sync(self) -> bool:
        if not self._get_enabled() or self._normalized_folder() is None:
            return False
        try:
            return bool(self._store.has_dirty_label_sync_rows())
        except Exception:
            return True

    def _debounced_run(self) -> None:
        with self._lock:
            self._debounce_timer = None
        self.sync_now()

    def _normalized_folder(self) -> str | None:
        raw = self._get_folder()
        if not raw:
            return None
        try:
            path = Path(str(raw)).expanduser().resolve()
        except (TypeError, ValueError, OSError):
            return None
        return str(path)

    def _sync_file_path(self) -> Path | None:
        folder = self._normalized_folder()
        if folder is None:
            return None
        return Path(folder) / SYNC_RELATIVE_PATH

    def _read_remote_digest(self) -> str | None:
        path = self._sync_file_path()
        if path is None or not path.is_file():
            return None
        try:
            return _digest_bytes(path.read_bytes())
        except OSError:
            return None

    def _note_local_write(self) -> None:
        self._ignore_watch_until = time.monotonic() + _WATCH_SUPPRESS_SEC

    @staticmethod
    def _maps_equal(left: LabelMap, right: LabelMap) -> bool:
        return dumps_label_map(left) == dumps_label_map(right)

    def _sync_with_store(self, remote_store: FolderRemoteStore) -> _SyncResult:
        for attempt in range(_MAX_PUT_RETRIES):
            try:
                remote_obj = remote_store.get(SYNC_RELATIVE_PATH)
                remote_map: LabelMap = (
                    loads_label_map(remote_obj.data) if remote_obj is not None else {}
                )
                local_map: LabelMap = self._store.export_label_sync_map()
                merged = merge_label_maps(local_map, remote_map)
                payload = dumps_label_map(merged)
                # Compare maps, not raw bytes — cloud clients may rewrite formatting.
                remote_unchanged = self._maps_equal(remote_map, merged)
                local_unchanged = self._maps_equal(local_map, merged)
                dirty = bool(self._store.has_dirty_label_sync_rows())

                if remote_unchanged and local_unchanged:
                    if dirty:
                        self._store.clear_label_sync_dirty()
                    if remote_obj is not None:
                        self._last_remote_digest = _digest_bytes(remote_obj.data)
                    else:
                        self._last_remote_digest = _digest_bytes(payload)
                    return _SyncResult(ok=True, changed=False)

                if not remote_unchanged:
                    if_match = remote_obj.etag if remote_obj is not None else None
                    self._note_local_write()
                    try:
                        remote_store.put(
                            SYNC_RELATIVE_PATH,
                            payload,
                            if_match=if_match,
                        )
                    except ConflictError:
                        if attempt + 1 >= _MAX_PUT_RETRIES:
                            self._record_error("Labels sync conflict; too many retries")
                            return _SyncResult(ok=False, changed=False)
                        continue
                    self._last_remote_digest = _digest_bytes(payload)

                changed = False
                if not local_unchanged:
                    self._store.apply_label_sync_map(merged, clear_dirty=True)
                    changed = True
                elif dirty:
                    self._store.clear_label_sync_dirty()
                if remote_unchanged and remote_obj is not None:
                    self._last_remote_digest = _digest_bytes(remote_obj.data)
                return _SyncResult(ok=True, changed=changed)
            except OSError as exc:
                try:
                    remote_obj = remote_store.get(SYNC_RELATIVE_PATH)
                except OSError:
                    self._record_error(str(exc))
                    return _SyncResult(ok=False, changed=False)
                if remote_obj is None:
                    self._record_error(str(exc))
                    return _SyncResult(ok=False, changed=False)
                remote_map = loads_label_map(remote_obj.data)
                local_map = self._store.export_label_sync_map()
                dirty_keys = self._dirty_key_set()
                merged = merge_label_maps(local_map, remote_map)
                changed = not self._maps_equal(local_map, merged)
                if changed:
                    self._store.apply_label_sync_map(merged, clear_dirty=True)
                    self._restore_dirty_keys(dirty_keys, local_map, merged)
                self._last_remote_digest = _digest_bytes(remote_obj.data)
                self._record_error(str(exc))
                return _SyncResult(ok=False, changed=changed)
        return _SyncResult(ok=False, changed=False)

    def _dirty_key_set(self) -> set[tuple[str, str]]:
        list_dirty = getattr(self._store, "list_dirty_label_keys", None)
        if callable(list_dirty):
            return set(list_dirty())
        return set()

    def _restore_dirty_keys(
        self,
        dirty_keys: set[tuple[str, str]],
        local_map: LabelMap,
        merged: LabelMap,
    ) -> None:
        mark = getattr(self._store, "mark_label_keys_dirty", None)
        if not callable(mark) or not dirty_keys:
            return
        keep: list[tuple[str, str]] = []
        for release_id, name in dirty_keys:
            local_entry = local_map.get(release_id, {}).get(name)
            merged_entry = merged.get(release_id, {}).get(name)
            if local_entry is not None and local_entry == merged_entry:
                keep.append((release_id, name))
        if keep:
            mark(keep)

    def _record_error(self, message: str) -> None:
        self._last_error = message
        self._persist_status()

    def _persist_status(self) -> None:
        if self._set_status is None:
            return
        try:
            self._set_status(self._last_success_at, self._last_error)
        except Exception:
            logger.debug("failed to persist label sync status", exc_info=True)
