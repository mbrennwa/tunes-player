"""Label sync lifecycle: startup catch-up only (no continuous file watch).

OwnCloud/Nextcloud desktop clients rewrite and touch sync files often enough that
a Gio file monitor caused repeated sync → UI grid rebuild loops. Live pickup
from other machines is deferred to startup, post-edit push/pull, and quit flush.
A future periodic poll can cover mid-session remote edits without Gio echo.
"""

from __future__ import annotations

import threading

import tunes_player.gi_bootstrap  # noqa: F401 — before gi.repository
import gi

gi.require_version("GLib", "2.0")

from gi.repository import GLib  # noqa: E402

from tunes_player.core.services import PlayerService


class LabelSyncWatcher:
    """Run label sync on startup and when sync settings change (no file monitor)."""

    def __init__(self, service: PlayerService) -> None:
        self._service = service
        self._unsubscribe = service.subscribe(self._on_service_event)

    def start(self) -> None:
        status = self._service.labels_sync_status()
        if status.enabled and status.folder:
            GLib.idle_add(self._startup_sync_idle)

    def stop(self) -> None:
        self._unsubscribe()

    def _on_service_event(self, event: str) -> None:
        if event == "labels_sync_changed":
            GLib.idle_add(self._on_settings_changed_idle)

    def _on_settings_changed_idle(self) -> bool:
        status = self._service.labels_sync_status()
        if status.enabled and status.folder:
            self._run_sync_in_background()
        return False

    def _startup_sync_idle(self) -> bool:
        self._run_sync_in_background()
        return False

    def _run_sync_in_background(self) -> None:
        thread = threading.Thread(
            target=self._service.sync_labels_now,
            name="label-sync",
            daemon=True,
        )
        thread.start()
