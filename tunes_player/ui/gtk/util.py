"""Shared GTK helpers."""

from __future__ import annotations

import shutil
import subprocess
import webbrowser
from collections.abc import Callable, Sequence
from importlib import resources

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from tunes_player.core.models import Release, Source, Track


def load_app_css() -> None:
    """Load application Gtk CSS (release grid tiles, etc.)."""
    css_path = resources.files("tunes_player.ui.gtk").joinpath("style.css")
    provider = Gtk.CssProvider()
    provider.load_from_path(str(css_path))
    display = Gdk.Display.get_default()
    if display is None:
        return
    Gtk.StyleContext.add_provider_for_display(
        display,
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


def escape_markup(text: str | None) -> str:
    """Escape text for Adw/Gtk widgets that interpret title as Pango markup."""
    if not text:
        return ""
    return GLib.markup_escape_text(text, -1)


def source_label(source: Source) -> str:
    """Human-readable catalog source name for UI badges and subtitles."""
    labels = {
        Source.LOCAL: "Local",
        Source.TIDAL: "TIDAL",
        Source.QOBUZ: "Qobuz",
    }
    return labels.get(source, source.value.capitalize())


def format_track_number(track: Track, *, fallback: int | None = None) -> str | None:
    """Display track index for release track lists (e.g. 3 or 2-5 for multi-disc)."""
    number = track.track_number if track.track_number is not None else fallback
    if number is None:
        return None
    if track.disc_number is not None and track.disc_number > 1:
        return f"{track.disc_number}-{number}"
    return str(number)


def join_detail(*parts: str | None) -> str:
    """Join non-empty subtitle segments with middle dots."""
    return " · ".join(part for part in parts if part)


def tracks_have_mixed_artists(tracks: Sequence[Track]) -> bool:
    """True when track artists are not all the same (mixed-artist release)."""
    artists = {t.artist_name for t in tracks if t.artist_name}
    return len(artists) > 1


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = round(seconds)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def effective_release_duration_sec(
    release: Release,
    tracks: list[Track] | None = None,
) -> float | None:
    """Release duration from metadata, or summed from loaded tracks."""
    if release.duration_sec is not None:
        return release.duration_sec
    if not tracks:
        return None
    total = sum(float(track.duration_sec or 0) for track in tracks)
    return total if total > 0 else None


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


def read_clipboard_text(on_ready: Callable[[str | None], None]) -> None:
    """Read plain text from the clipboard (async); calls on_ready on the main loop."""
    display = Gdk.Display.get_default()
    if display is None:
        on_ready(None)
        return
    clipboard = display.get_clipboard()

    def finish(_clip: Gdk.Clipboard, result: Gio.AsyncResult, _user_data: object) -> None:
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error:
            text = None
        on_ready(text.strip() if text else None)

    clipboard.read_text_async(None, finish, None)
