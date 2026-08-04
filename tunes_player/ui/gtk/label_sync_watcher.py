"""Label sync lifecycle: startup, settings changes, and slow periodic poll.

OwnCloud/Nextcloud desktop clients rewrite and touch sync files often enough that
a Gio file monitor caused repeated sync → UI grid rebuild loops. Mid-session
pickup from other machines uses a slow poll (digest check first) plus startup,
post-edit push/pull, and quit flush — not a continuous file watch.
"""

from __future__ import annotations

import threading

import tunes_player.gi_bootstrap  # noqa: F401 — before gi.repository
import gi

gi.require_version("GLib", "2.0")

from gi.repository import GLib  # noqa: E402

from tunes_player.core.services import PlayerService

# Slow enough to avoid OwnCloud echo thrash; fast enough for casual mid-session pickup.
_POLL_INTERVAL_MS = 150_000  # 2.5 minutes


class LabelSyncWatcher:
    """Run label sync on startup, settings change, and a slow periodic poll."""

    def __init__(self, service: PlayerService) -> None:
        self._service = service
        self._unsubscribe = service.subscribe(self._on_service_event)
        self._poll_source_id: int | None = None

    def start(self) -> None:
        status = self._service.labels_sync_status()
        if status.enabled and status.folder:
            GLib.idle_add(self._startup_sync_idle)
            self._ensure_poll_timer()

    def stop(self) -> None:
        self._unsubscribe()
        self._clear_poll_timer()

    def _on_service_event(self, event: str) -> None:
        if event == "labels_sync_changed":
            GLib.idle_add(self._on_settings_changed_idle)

    def _on_settings_changed_idle(self) -> bool:
        status = self._service.labels_sync_status()
        if status.enabled and status.folder:
            self._ensure_poll_timer()
            self._run_sync_in_background()
        else:
            self._clear_poll_timer()
        return False

    def _startup_sync_idle(self) -> bool:
        self._run_sync_in_background()
        return False

    def _ensure_poll_timer(self) -> None:
        if self._poll_source_id is not None:
            return
        self._poll_source_id = GLib.timeout_add(_POLL_INTERVAL_MS, self._on_poll_timeout)

    def _clear_poll_timer(self) -> None:
        if self._poll_source_id is None:
            return
        GLib.source_remove(self._poll_source_id)
        self._poll_source_id = None

    def _on_poll_timeout(self) -> bool:
        status = self._service.labels_sync_status()
        if not status.enabled or not status.folder:
            self._poll_source_id = None
            return False
        if self._service.labels_sync_ignore_watch_events():
            return True
        if self._service.labels_sync_remote_unchanged():
            return True
        self._run_sync_in_background()
        return True

    def _run_sync_in_background(self) -> None:
        thread = threading.Thread(
            target=self._service.sync_labels_now,
            name="label-sync",
            daemon=True,
        )
        thread.start()
