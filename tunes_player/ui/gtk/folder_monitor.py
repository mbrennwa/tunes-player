"""Background rescan and filesystem monitoring for local music folders."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")

from gi.repository import Gio, GLib  # noqa: E402

from tunes_player.core.services import PlayerService

_DEBOUNCE_MS = 5000
_PERIODIC_SEC = 600

_WATCH_EVENTS = frozenset(
    {
        Gio.FileMonitorEvent.CREATED,
        Gio.FileMonitorEvent.DELETED,
        Gio.FileMonitorEvent.CHANGES_DONE_HINT,
        Gio.FileMonitorEvent.MOVED_IN,
        Gio.FileMonitorEvent.MOVED_OUT,
    }
)


class FolderMonitorManager:
    """Watch auto-monitor folders and queue incremental rescans in the background."""

    def __init__(self, service: PlayerService) -> None:
        self._service = service
        self._monitors: dict[str, Gio.FileMonitor] = {}
        self._debounce_ids: dict[str, int] = {}
        self._periodic_source_id: int | None = None
        self._unsubscribe = service.subscribe(self._on_service_event)

    def start(self) -> None:
        self._sync_monitors()
        if self._periodic_source_id is None:
            self._periodic_source_id = GLib.timeout_add_seconds(
                _PERIODIC_SEC,
                self._on_periodic,
            )
        self._service.enqueue_startup_scans()

    def stop(self) -> None:
        if self._periodic_source_id is not None:
            GLib.source_remove(self._periodic_source_id)
            self._periodic_source_id = None
        for folder in list(self._monitors):
            self._clear_monitor(folder)
        for source_id in list(self._debounce_ids.values()):
            GLib.source_remove(source_id)
        self._debounce_ids.clear()
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
        _file: Gio.File,
        _other_file: Gio.File,
        event: Gio.FileMonitorEvent,
        folder: str,
    ) -> None:
        if not self._service.folder_auto_monitor_enabled(folder):
            return
        if event not in _WATCH_EVENTS:
            return
        self._schedule_debounced_scan(folder)

    def _schedule_debounced_scan(self, folder: str) -> None:
        existing = self._debounce_ids.get(folder)
        if existing is not None:
            GLib.source_remove(existing)
        self._debounce_ids[folder] = GLib.timeout_add(
            _DEBOUNCE_MS,
            self._run_debounced_scan,
            folder,
        )

    def _run_debounced_scan(self, folder: str) -> bool:
        self._debounce_ids.pop(folder, None)
        if self._service.folder_auto_monitor_enabled(folder):
            self._service.enqueue_scan(folder=folder)
        return False

    def _on_periodic(self) -> bool:
        for folder in self._service.config.config.music_folders:
            if self._service.folder_auto_monitor_enabled(folder):
                self._service.enqueue_scan(folder=folder)
        return True

    def _clear_monitor(self, folder: str) -> None:
        monitor = self._monitors.pop(folder, None)
        if monitor is not None:
            monitor.cancel()
        debounce_id = self._debounce_ids.pop(folder, None)
        if debounce_id is not None:
            GLib.source_remove(debounce_id)
