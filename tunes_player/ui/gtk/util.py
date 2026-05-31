"""Shared GTK helpers."""

from __future__ import annotations

import shutil
import subprocess
import webbrowser

import gi

gi.require_version("Gio", "2.0")

from gi.repository import Gio, GLib  # noqa: E402


def escape_markup(text: str | None) -> str:
    """Escape text for Adw/Gtk widgets that interpret title as Pango markup."""
    if not text:
        return ""
    return GLib.markup_escape_text(text, -1)


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def normalize_http_uri(uri: str) -> str:
    """Ensure a browsable URL (tidalapi OAuth links omit https://)."""
    value = uri.strip()
    if not value or "://" in value:
        return value
    return f"https://{value}"


def open_external_uri(uri: str) -> tuple[bool, str | None]:
    """Open a URI in the default browser. Returns (success, error_message)."""
    uri = normalize_http_uri(uri)
    last_error: str | None = None

    try:
        Gio.AppInfo.launch_default_for_uri(uri, None)
        return True, None
    except GLib.Error as exc:
        last_error = exc.message

    xdg_open = shutil.which("xdg-open")
    if xdg_open is not None:
        completed = subprocess.run([xdg_open, uri], check=False)
        if completed.returncode == 0:
            return True, None
        last_error = f"{xdg_open} exited with code {completed.returncode}"

    if webbrowser.open(uri):
        return True, None

    return False, last_error or "Could not open link"
