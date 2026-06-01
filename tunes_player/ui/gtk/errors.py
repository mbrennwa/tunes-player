"""User-visible error reporting (toasts)."""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, GLib  # noqa: E402

from tunes_player.core.services import PlayerService

_TOAST_TIMEOUT_SEC = 5


def show_error_toast(overlay: Adw.ToastOverlay, message: str) -> None:
    toast = Adw.Toast.new(message)
    toast.set_timeout(_TOAST_TIMEOUT_SEC)
    overlay.add_toast(toast)


def attach_error_toasts(window: Adw.ApplicationWindow, service: PlayerService) -> Adw.ToastOverlay:
    """Wrap window content in a toast overlay and show errors from PlayerService."""
    overlay = Adw.ToastOverlay()
    content = window.get_content()
    if content is not None:
        window.set_content(None)
        overlay.set_child(content)
    window.set_content(overlay)

    def on_event(event: str) -> bool:
        if event == "playback_error":
            GLib.idle_add(_show_playback_error, overlay, service)
        return False

    service.subscribe(lambda event: GLib.idle_add(on_event, event))
    return overlay


def _show_playback_error(overlay: Adw.ToastOverlay, service: PlayerService) -> bool:
    message = service.last_error() or "Playback failed"
    show_error_toast(overlay, message)
    return False
