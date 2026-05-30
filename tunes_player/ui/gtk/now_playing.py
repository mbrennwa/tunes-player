"""Persistent Now Playing transport bar."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from tunes_player.core.services import PlayerService
from tunes_player.ui.gtk.util import format_duration


class NowPlayingBar(Gtk.Box):
    """Bottom transport bar shared by expanded and minimized layouts."""

    def __init__(
        self,
        *,
        service: PlayerService,
        compact: bool = False,
        on_restore: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._service = service
        self._compact = compact
        self.add_css_class("toolbar")
        self.add_css_class("now-playing-bar")

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_margin_start(12)
        row.set_margin_end(12)
        row.set_margin_top(8)
        row.set_margin_bottom(8)
        self.append(row)

        self._restore_btn: Gtk.Button | None = None
        if on_restore is not None:
            self._restore_btn = Gtk.Button(icon_name="view-restore-symbolic")
            self._restore_btn.set_tooltip_text("Restore player")
            self._restore_btn.set_visible(compact)
            self._restore_btn.connect("clicked", lambda *_: on_restore())
            row.append(self._restore_btn)

        self._art = Gtk.Image.new_from_icon_name("audio-x-generic-symbolic")
        self._art.set_pixel_size(48 if not compact else 32)
        self._art.add_css_class("card")
        row.append(self._art)

        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        meta.set_hexpand(True)
        meta.set_valign(Gtk.Align.CENTER)
        row.append(meta)

        self._title = Gtk.Label(label="Not playing", xalign=0, ellipsize=3)
        self._title.add_css_class("heading")
        meta.append(self._title)

        self._subtitle = Gtk.Label(label="Select an album or track", xalign=0, ellipsize=3)
        self._subtitle.add_css_class("dim-label")
        meta.append(self._subtitle)

        self._quality: Gtk.Label | None = None
        if not compact:
            self._quality = Gtk.Label(label="", xalign=0, ellipsize=3)
            self._quality.add_css_class("caption")
            self._quality.add_css_class("dim-label")
            meta.append(self._quality)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        controls.set_valign(Gtk.Align.CENTER)
        row.append(controls)

        prev_btn = Gtk.Button()
        prev_btn.set_icon_name("media-skip-backward-symbolic")
        prev_btn.set_tooltip_text("Previous")
        prev_btn.connect("clicked", lambda *_: service.skip_previous())
        controls.append(prev_btn)

        self._play_btn = Gtk.Button()
        self._play_btn.add_css_class("suggested-action")
        self._play_btn.set_icon_name("media-playback-start-symbolic")
        self._play_btn.set_tooltip_text("Play")
        self._play_btn.connect("clicked", lambda *_: service.toggle_play_pause())
        controls.append(self._play_btn)

        next_btn = Gtk.Button()
        next_btn.set_icon_name("media-skip-forward-symbolic")
        next_btn.set_tooltip_text("Next")
        next_btn.connect("clicked", lambda *_: service.skip_next())
        controls.append(next_btn)

        self._volume: Gtk.Scale | None = None
        if not compact:
            volume_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            volume_box.set_valign(Gtk.Align.CENTER)
            controls.append(volume_box)

            mute_btn = Gtk.Button()
            mute_btn.set_icon_name("audio-volume-high-symbolic")
            mute_btn.set_tooltip_text("Volume")
            volume_box.append(mute_btn)

            self._volume = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL,
                0.0,
                1.0,
                0.01,
            )
            self._volume.set_size_request(120, -1)
            self._volume.set_draw_value(False)
            self._volume.connect("value-changed", self._on_volume_changed)
            volume_box.append(self._volume)

            queue_btn = Gtk.Button()
            queue_btn.set_icon_name("view-list-symbolic")
            queue_btn.set_tooltip_text("Queue")
            queue_btn.connect("clicked", self._on_queue_clicked)
            controls.append(queue_btn)

        self._queue_handler: Callable[[], None] | None = None
        service.subscribe(lambda _event: GLib.idle_add(self._sync_from_service))
        self._sync_from_service()

    def set_queue_handler(self, handler: Callable[[], None]) -> None:
        self._queue_handler = handler

    def set_compact(self, compact: bool) -> None:
        self._compact = compact
        self._art.set_pixel_size(32 if compact else 48)
        if self._restore_btn is not None:
            self._restore_btn.set_visible(compact)

    def _on_queue_clicked(self, *_args: object) -> None:
        if self._queue_handler is not None:
            self._queue_handler()

    def _on_volume_changed(self, scale: Gtk.Scale) -> None:
        if scale.get_value() != self._service.get_playback_state().volume:
            self._service.set_volume(scale.get_value())

    def _sync_from_service(self) -> bool:
        state = self._service.get_playback_state()
        track = state.current_track

        if track is None:
            self._title.set_label("Not playing")
            self._subtitle.set_label("Select an album or track")
            if self._quality is not None:
                self._quality.set_label("")
            self._play_btn.set_icon_name("media-playback-start-symbolic")
            self._play_btn.set_tooltip_text("Play")
        else:
            self._title.set_label(track.title)
            artist = track.artist_name
            album = track.album_title or ""
            duration = format_duration(track.duration_sec)
            self._subtitle.set_label(f"{artist} · {album} · {duration}".strip(" · "))
            if self._quality is not None:
                badges = [state.quality_hint]
                if state.bit_perfect:
                    badges.append("bit-perfect")
                if state.device_volume:
                    badges.append("device volume")
                self._quality.set_label(" · ".join(badges))
            icon = (
                "media-playback-pause-symbolic"
                if state.is_playing
                else "media-playback-start-symbolic"
            )
            self._play_btn.set_icon_name(icon)
            self._play_btn.set_tooltip_text("Pause" if state.is_playing else "Play")

        if self._volume is not None:
            self._volume.handler_block_by_func(self._on_volume_changed)
            self._volume.set_value(state.volume)
            self._volume.handler_unblock_by_func(self._on_volume_changed)

        return False


def attach_media_keys(window: Gtk.Widget, service: PlayerService) -> None:
    controller = Gtk.EventControllerKey.new()
    controller.connect(
        "key-pressed",
        lambda _ctrl, _n, keyval, _state: _handle_media_key(keyval, service),
    )
    window.add_controller(controller)


def _handle_media_key(keyval: int, service: PlayerService) -> bool:
    if keyval in (Gdk.KEY_XF86AudioPlay, Gdk.KEY_XF86AudioPause):
        service.toggle_play_pause()
        return True
    if keyval == Gdk.KEY_XF86AudioNext:
        service.skip_next()
        return True
    if keyval == Gdk.KEY_XF86AudioPrev:
        service.skip_previous()
        return True
    if keyval == Gdk.KEY_XF86AudioStop:
        service.pause()
        return True
    if keyval == Gdk.KEY_XF86AudioRaiseVolume:
        service.adjust_volume(0.05)
        return True
    if keyval == Gdk.KEY_XF86AudioLowerVolume:
        service.adjust_volume(-0.05)
        return True
    return False
