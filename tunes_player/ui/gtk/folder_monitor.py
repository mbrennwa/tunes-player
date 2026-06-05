"""Background rescan and filesystem monitoring for local music folders."""

from __future__ import annotations

import os
from pathlib import Path

import tunes_player.gi_bootstrap  # noqa: F401 — before gi.repository
import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")

from gi.repository import Gio, GLib  # noqa: E402

from tunes_player.core.services import PlayerService

_DEBOUNCE_MS = 5000

_ADD_EVENTS = frozenset(
    {
        Gio.FileMonitorEvent.CREATED,
        Gio.FileMonitorEvent.MOVED_IN,
        Gio.FileMonitorEvent.CHANGES_DONE_HINT,
    },
)
_REMOVE_EVENTS = frozenset(
    {
        Gio.FileMonitorEvent.DELETED,
        Gio.FileMonitorEvent.MOVED_OUT,
    },
)


class FolderMonitorManager:
    """Watch auto-monitor folders and apply incremental library updates."""

    def __init__(self, service: PlayerService) -> None:
        self._service = service
        self._monitors: dict[str, Gio.FileMonitor] = {}
        self._debounce_ids: dict[str, int] = {}
        self._pending_adds: dict[str, set[str]] = {}
        self._pending_removes: dict[str, set[str]] = {}
        self._unsubscribe = service.subscribe(self._on_service_event)

    def start(self) -> None:
        self._sync_monitors()
        self._service.enqueue_startup_scans()

    def stop(self) -> None:
        for folder in list(self._monitors):
            self._clear_monitor(folder)
        for source_id in list(self._debounce_ids.values()):
            GLib.source_remove(source_id)
        self._debounce_ids.clear()
        self._pending_adds.clear()
        self._pending_removes.clear()
        self._unsubscribe()

    def _on_service_event(self, event: str) -> None:
        if event == "sources_changed":
            GLib.idle_add(self._sync_monitors_idle)

    def _sync_monitors_idle(self) -> bool:
        self._sync_monitors()
        return False

    def _sync_monitors(self) -> None:
        enabled = {
            str(Path(folder).expanduser().resolve())
            for folder in self._service.config.config.music_folders
            if self._service.folder_auto_monitor_enabled(folder)
        }
        for folder in list(self._monitors):
            if folder not in enabled:
                self._clear_monitor(folder)
        for folder in enabled:
            if folder not in self._monitors:
                self._attach_monitor(folder)

    def _attach_monitor(self, folder: str) -> None:
        root = Path(folder)
        if not root.is_dir():
            return
        try:
            gfile = Gio.File.new_for_path(str(root))
            monitor = gfile.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        except GLib.Error:
            return
        monitor.connect("changed", self._on_changed, folder)
        self._monitors[folder] = monitor

    def _on_changed(
        self,
        _monitor: Gio.FileMonitor,
        file: Gio.File,
        _other_file: Gio.File,
        event: Gio.FileMonitorEvent,
        folder: str,
    ) -> None:
        if not self._service.folder_auto_monitor_enabled(folder):
            return
        path = self._path_from_file(file)
        if path is None:
            self._schedule_debounced_full_scan(folder)
            return
        if event in _ADD_EVENTS:
            self._record_add(folder, path)
        elif event in _REMOVE_EVENTS:
            self._record_remove(folder, path)
        else:
            return
        self._schedule_debounced_incremental_scan(folder)

    @staticmethod
    def _path_from_file(file: Gio.File) -> str | None:
        raw = file.get_path()
        if raw is None:
            return None
        return str(Path(raw).resolve())

    def _record_add(self, folder: str, path: str) -> None:
        if not self._path_under_folder(path, folder):
            return
        adds = self._pending_adds.setdefault(folder, set())
        removes = self._pending_removes.setdefault(folder, set())
        removes.discard(path)
        adds.add(path)

    def _record_remove(self, folder: str, path: str) -> None:
        if not self._path_under_folder(path, folder):
            return
        adds = self._pending_adds.setdefault(folder, set())
        removes = self._pending_removes.setdefault(folder, set())
        if path in adds:
            adds.discard(path)
            return
        removes.add(path)

    @staticmethod
    def _path_under_folder(path: str, folder: str) -> bool:
        folder_resolved = str(Path(folder).resolve())
        return path == folder_resolved or path.startswith(folder_resolved + os.sep)

    def _schedule_debounced_incremental_scan(self, folder: str) -> None:
        existing = self._debounce_ids.get(folder)
        if existing is not None:
            GLib.source_remove(existing)
        self._debounce_ids[folder] = GLib.timeout_add(
            _DEBOUNCE_MS,
            self._run_debounced_incremental_scan,
            folder,
        )

    def _schedule_debounced_full_scan(self, folder: str) -> None:
        existing = self._debounce_ids.get(folder)
        if existing is not None:
            GLib.source_remove(existing)
        self._pending_adds.pop(folder, None)
        self._pending_removes.pop(folder, None)
        self._debounce_ids[folder] = GLib.timeout_add(
            _DEBOUNCE_MS,
            self._run_debounced_full_scan,
            folder,
        )

    def _run_debounced_incremental_scan(self, folder: str) -> bool:
        self._debounce_ids.pop(folder, None)
        if not self._service.folder_auto_monitor_enabled(folder):
            return False
        add_paths = sorted(self._pending_adds.pop(folder, set()))
        remove_paths = sorted(self._pending_removes.pop(folder, set()))
        if not add_paths and not remove_paths:
            return False
        self._service.enqueue_incremental_scan(
            folder=folder,
            add_paths=add_paths,
            remove_paths=remove_paths,
        )
        return False

    def _run_debounced_full_scan(self, folder: str) -> bool:
        self._debounce_ids.pop(folder, None)
        if self._service.folder_auto_monitor_enabled(folder):
            self._service.enqueue_scan(folder=folder)
        return False

    def _clear_monitor(self, folder: str) -> None:
        monitor = self._monitors.pop(folder, None)
        if monitor is not None:
            monitor.cancel()
        debounce_id = self._debounce_ids.pop(folder, None)
        if debounce_id is not None:
            GLib.source_remove(debounce_id)
        self._pending_adds.pop(folder, None)
        self._pending_removes.pop(folder, None)
