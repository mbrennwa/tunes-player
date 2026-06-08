"""Persistent Now Playing transport bar."""

from __future__ import annotations

import time
from collections.abc import Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from tunes_player.core.services import PlaybackState, PlayerService
from tunes_player.ui.gtk.art import ArtLoader
from tunes_player.ui.gtk.util import format_duration, join_detail, source_label

_TIME_LABEL_CHARS = 7  # wide enough for "0:00:00"
# Keep UI seeks inside mpv-safe range (see playback_client._SEEK_END_MARGIN_SEC).
_SEEK_END_MARGIN_SEC = 1.0
_ART_PIXEL_SIZE = 48
_VOLUME_SLIDER_WIDTH = 180
_CONTROLS_WIDTH = 388  # prev · play · next · volume · queue + spacing
_VOLUME_BOX_WIDTH = 224  # mute button + volume slider + spacing


def _keyval(name: str) -> int:
    """Resolve a media key keyval (GDK 4 has no Gdk.KEY_XF86* constants)."""
    return Gdk.keyval_from_name(name)


# Resolved once at import; keyval_from_name works on GDK 4 where KEY_XF86* attrs do not.
_KEY_PLAY_PAUSE = (_keyval("XF86AudioPlay"), _keyval("XF86AudioPause"))
_KEY_NEXT = _keyval("XF86AudioNext")
_KEY_PREV = _keyval("XF86AudioPrev")
_KEY_STOP = _keyval("XF86AudioStop")
_KEY_VOLUME_UP = _keyval("XF86AudioRaiseVolume")
_KEY_VOLUME_DOWN = _keyval("XF86AudioLowerVolume")


def _volume_icon_name(level: float, *, muted: bool) -> str:
    if muted or level <= 0.01:
        return "audio-volume-muted-symbolic"
    if level < 0.33:
        return "audio-volume-low-symbolic"
    if level < 0.66:
        return "audio-volume-medium-symbolic"
    return "audio-volume-high-symbolic"


class NowPlayingBar(Gtk.Box):
    """Bottom transport bar."""

    def __init__(
        self,
        *,
        service: PlayerService,
        art_loader: ArtLoader | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_hexpand(True)
        self._service = service
        self._art_loader = art_loader
        self._art_track_id: str | None = None
        self.add_css_class("toolbar")
        self.add_css_class("now-playing-bar")

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_hexpand(True)
        row.set_margin_start(12)
        row.set_margin_end(12)
        row.set_margin_top(8)
        row.set_margin_bottom(8)
        self.append(row)

        self._art = Gtk.Box()
        self._art.add_css_class("now-playing-art")
        self._art.set_size_request(_ART_PIXEL_SIZE, _ART_PIXEL_SIZE)
        self._art.set_overflow(Gtk.Overflow.HIDDEN)
        self._art.set_hexpand(False)
        self._art.set_vexpand(False)
        self._art.set_halign(Gtk.Align.CENTER)
        self._art.set_valign(Gtk.Align.CENTER)

        self._art_picture = Gtk.Picture()
        self._art_picture.set_content_fit(Gtk.ContentFit.COVER)
        self._art_picture.set_can_shrink(True)
        self._art_picture.set_size_request(_ART_PIXEL_SIZE, _ART_PIXEL_SIZE)
        self._art_picture.set_hexpand(False)
        self._art_picture.set_vexpand(False)
        self._art_picture.set_halign(Gtk.Align.FILL)
        self._art_picture.set_valign(Gtk.Align.FILL)
        self._art.append(self._art_picture)

        self._attach_art_click(self._art)
        row.append(self._art)

        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        meta.add_css_class("now-playing-meta")
        meta.set_hexpand(True)
        meta.set_valign(Gtk.Align.CENTER)
        row.append(meta)

        self._title = Gtk.Label(label="Not playing", xalign=0, ellipsize=3)
        self._title.add_css_class("heading")
        meta.append(self._title)

        self._subtitle = Gtk.Label(label="Select an album or track", xalign=0, ellipsize=3)
        self._subtitle.add_css_class("dim-label")
        meta.append(self._subtitle)

        self._quality = Gtk.Label(label="", xalign=0, ellipsize=3)
        self._quality.add_css_class("caption")
        self._quality.add_css_class("dim-label")
        meta.append(self._quality)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        controls.add_css_class("now-playing-controls")
        controls.set_hexpand(False)
        controls.set_halign(Gtk.Align.END)
        controls.set_size_request(_CONTROLS_WIDTH, -1)
        controls.set_valign(Gtk.Align.CENTER)
        row.append(controls)

        self._controls_leading = Gtk.Box()
        controls.append(self._controls_leading)

        prev_btn = Gtk.Button()
        prev_btn.add_css_class("circular")
        prev_btn.set_icon_name("media-skip-backward-symbolic")
        prev_btn.set_tooltip_text("Previous")
        prev_btn.connect("clicked", lambda *_: service.skip_previous())
        controls.append(prev_btn)

        self._play_btn = Gtk.Button()
        self._play_btn.add_css_class("suggested-action")
        self._play_btn.add_css_class("circular")
        self._play_btn.set_icon_name("media-playback-start-symbolic")
        self._play_btn.set_tooltip_text("Play")
        self._play_handler: Callable[[], None] | None = None
        self._play_btn.connect("clicked", self._on_play_clicked)
        controls.append(self._play_btn)

        next_btn = Gtk.Button()
        next_btn.add_css_class("circular")
        next_btn.set_icon_name("media-skip-forward-symbolic")
        next_btn.set_tooltip_text("Next")
        next_btn.connect("clicked", lambda *_: service.skip_next())
        controls.append(next_btn)

        self._volume_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._volume_box.set_size_request(_VOLUME_BOX_WIDTH, -1)
        self._volume_box.set_valign(Gtk.Align.CENTER)
        controls.append(self._volume_box)

        self._mute_btn = Gtk.Button()
        self._mute_btn.set_icon_name("audio-volume-high-symbolic")
        self._mute_btn.set_tooltip_text("Mute")
        self._mute_btn.connect("clicked", self._on_mute_clicked)
        self._volume_box.append(self._mute_btn)

        self._volume = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL,
            0.0,
            1.0,
            0.01,
        )
        self._volume.set_hexpand(False)
        self._volume.set_size_request(_VOLUME_SLIDER_WIDTH, -1)
        self._volume.set_draw_value(False)
        self._volume.connect("value-changed", self._on_volume_changed)
        self._attach_drag_gesture(
            self._volume,
            self._begin_volume_drag,
            self._end_volume_drag,
        )
        self._volume_box.append(self._volume)

        queue_btn = Gtk.Button()
        queue_btn.set_icon_name("view-list-symbolic")
        queue_btn.set_tooltip_text("Queue")
        queue_btn.connect("clicked", self._on_queue_clicked)
        controls.append(queue_btn)

        self._updating_progress = False
        self._progress_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._progress_row.set_margin_start(12)
        self._progress_row.set_margin_end(12)
        self._progress_row.set_margin_bottom(8)
        self.append(self._progress_row)

        self._elapsed = Gtk.Label(label="0:00", xalign=0)
        self._elapsed.set_width_chars(_TIME_LABEL_CHARS)
        self._elapsed.add_css_class("caption")
        self._elapsed.add_css_class("dim-label")
        self._elapsed.add_css_class("numeric")
        self._progress_row.append(self._elapsed)

        self._progress = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL,
            0.0,
            1.0,
            0.001,
        )
        self._progress.set_hexpand(True)
        self._progress.set_draw_value(False)
        self._progress.set_sensitive(False)
        self._progress.connect("change-value", self._on_progress_change_value)
        self._progress.connect("value-changed", self._on_progress_value_changed)
        self._attach_drag_gesture(
            self._progress,
            self._begin_seek_drag,
            self._end_seek_drag,
        )
        self._progress_row.append(self._progress)

        self._remaining = Gtk.Label(label="0:00", xalign=1)
        self._remaining.set_width_chars(_TIME_LABEL_CHARS)
        self._remaining.add_css_class("caption")
        self._remaining.add_css_class("dim-label")
        self._remaining.add_css_class("numeric")
        self._progress_row.append(self._remaining)

        self._queue_handler: Callable[[], None] | None = None
        self._art_click_handler: Callable[[], None] | None = None
        self._volume_dragging = False
        self._pending_volume: float | None = None
        self._volume_apply_id: int | None = None
        self._seeking = False
        self._pending_seek: float | None = None
        self._seek_apply_id: int | None = None
        self._progress_track_id: str | None = None
        self._shown_sec = 0.0
        self._shown_anchor_sec = 0.0
        self._shown_anchor_at: float | None = None
        service.subscribe(self._on_service_event)
        self._sync_from_service()
        GLib.timeout_add(33, self._tick_progress)

    def set_queue_handler(self, handler: Callable[[], None]) -> None:
        self._queue_handler = handler

    def set_art_click_handler(self, handler: Callable[[], None] | None) -> None:
        self._art_click_handler = handler

    def set_play_handler(self, handler: Callable[[], None]) -> None:
        self._play_handler = handler

    def _attach_art_click(self, widget: Gtk.Widget) -> None:
        gesture = Gtk.GestureClick()
        gesture.connect("released", self._on_art_released)
        widget.add_controller(gesture)
        widget.set_focusable(True)
        widget.set_cursor_from_name("default")

    def _on_art_released(self, _gesture: Gtk.GestureClick, _n_press: int, _x: float, _y: float) -> None:
        handler = self._art_click_handler
        if handler is not None:
            handler()

    def _sync_art_clickable(self, track: object | None) -> None:
        clickable = track is not None and self._art_click_handler is not None
        self._art.set_sensitive(clickable)
        self._art.set_cursor_from_name("pointer" if clickable else "default")
        self._art.set_tooltip_text("View album" if clickable else None)

    def _on_play_clicked(self, *_args: object) -> None:
        handler = self._play_handler
        if handler is not None:
            handler()
            return
        self._service.toggle_play_pause()

    def _attach_drag_gesture(
        self,
        widget: Gtk.Widget,
        on_begin: Callable[[], None],
        on_end: Callable[[], None],
    ) -> None:
        gesture = Gtk.GestureDrag.new()
        gesture.connect("drag-begin", lambda *_: on_begin())
        gesture.connect("drag-end", lambda *_: on_end())
        widget.add_controller(gesture)

    def _begin_volume_drag(self) -> None:
        self._volume_dragging = True

    def _end_volume_drag(self) -> None:
        self._volume_dragging = False
        if self._volume_apply_id is not None:
            GLib.source_remove(self._volume_apply_id)
            self._volume_apply_id = None
        if self._pending_volume is not None:
            self._service.set_volume(self._pending_volume, notify=True)
            self._pending_volume = None

    def _schedule_volume_apply(self) -> None:
        if self._volume_apply_id is None:
            self._volume_apply_id = GLib.timeout_add(50, self._apply_pending_volume)

    def _apply_pending_volume(self) -> bool:
        self._volume_apply_id = None
        if self._pending_volume is not None:
            self._service.set_volume(self._pending_volume, notify=False)
        return False

    def _begin_seek_drag(self) -> None:
        self._seeking = True

    def _playback_duration(self, state: PlaybackState) -> float | None:
        duration = state.duration_sec
        if duration is None or duration <= 0:
            track = state.current_track
            if track is not None:
                catalog = track.duration_sec
                if catalog is not None and catalog > 0:
                    duration = catalog
        if duration is None or duration <= 0:
            return None
        return float(duration)

    def _clamp_seek_position(self, position_sec: float, duration_sec: float) -> float:
        position_sec = max(0.0, min(position_sec, duration_sec))
        safe_end = max(0.0, duration_sec - _SEEK_END_MARGIN_SEC)
        return min(position_sec, safe_end)

    def _end_seek_drag(self) -> None:
        if self._seek_apply_id is not None:
            GLib.source_remove(self._seek_apply_id)
            self._seek_apply_id = None
        if self._pending_seek is not None:
            self._apply_seek(self._pending_seek)
            self._pending_seek = None
        self._seeking = False

    def _on_progress_change_value(
        self,
        _scale: Gtk.Scale,
        _scroll_type: Gtk.ScrollType,
        value: float,
    ) -> bool:
        if self._updating_progress or self._seeking:
            return True
        duration = self._playback_duration(self._service.get_playback_state())
        if duration is None:
            return True
        self._apply_seek(value * duration)
        return True

    def _on_progress_value_changed(self, scale: Gtk.Scale) -> None:
        if self._updating_progress or not self._seeking:
            return
        duration = self._playback_duration(self._service.get_playback_state())
        if duration is None:
            return
        position_sec = self._clamp_seek_position(
            max(0.0, min(scale.get_value(), 1.0)) * duration,
            duration,
        )
        fraction = position_sec / duration
        if abs(scale.get_value() - fraction) > 0.001:
            self._set_progress_fraction(fraction)
        self._update_seek_labels(position_sec, duration)
        self._pending_seek = position_sec
        self._schedule_seek_apply()

    def _apply_seek(self, position_sec: float) -> None:
        duration = self._playback_duration(self._service.get_playback_state())
        if duration is None:
            return
        position_sec = self._clamp_seek_position(position_sec, duration)
        self._service.seek(position_sec)
        self._shown_sec = position_sec
        self._shown_anchor_sec = position_sec
        self._shown_anchor_at = time.monotonic()
        self._set_progress_fraction(position_sec / duration, allow_decrease=True)
        self._update_seek_labels(position_sec, duration)

    def _set_progress_fraction(self, fraction: float, *, allow_decrease: bool = False) -> None:
        fraction = max(0.0, min(fraction, 1.0))
        if not allow_decrease:
            fraction = max(self._progress.get_value(), fraction)
        self._updating_progress = True
        self._progress.handler_block_by_func(self._on_progress_change_value)
        self._progress.handler_block_by_func(self._on_progress_value_changed)
        self._progress.set_value(fraction)
        self._progress.handler_unblock_by_func(self._on_progress_value_changed)
        self._progress.handler_unblock_by_func(self._on_progress_change_value)
        self._updating_progress = False

    def _schedule_seek_apply(self) -> None:
        if self._seek_apply_id is None:
            self._seek_apply_id = GLib.timeout_add(120, self._apply_pending_seek)

    def _apply_pending_seek(self) -> bool:
        self._seek_apply_id = None
        if self._seeking and self._pending_seek is not None:
            self._apply_seek(self._pending_seek)
        return False

    def _update_seek_labels(self, position_sec: float, duration_sec: float) -> None:
        self._elapsed.set_label(format_duration(position_sec))
        self._remaining.set_label(format_duration(max(0.0, duration_sec - position_sec)))

    def _on_queue_clicked(self, *_args: object) -> None:
        if self._queue_handler is not None:
            self._queue_handler()

    def _on_mute_clicked(self, *_args: object) -> None:
        self._service.toggle_mute()

    def _sync_mute_button(self, state: object) -> None:
        icon = _volume_icon_name(state.volume, muted=state.muted)
        self._mute_btn.set_icon_name(icon)
        self._mute_btn.set_tooltip_text("Unmute" if state.muted else "Mute")

    def _on_volume_changed(self, scale: Gtk.Scale) -> None:
        if not self._service.volume_adjustable():
            return
        value = scale.get_value()
        if self._volume_dragging:
            self._pending_volume = value
            self._schedule_volume_apply()
            return
        self._service.set_volume(value)

    def _on_service_event(self, event: str) -> None:
        GLib.idle_add(self._sync_from_service, event)

    def _sync_from_service(self, event: str | None = None) -> bool:
        if event == "position_changed":
            return False

        if event == "art_updated":
            self._art_track_id = None

        state = self._service.get_playback_state()
        track = state.current_track

        if track is None:
            self._title.set_label("Not playing")
            self._subtitle.set_label("Select an album or track")
            self._quality.set_label("")
            self._play_btn.set_icon_name("media-playback-start-symbolic")
            self._play_btn.set_tooltip_text("Play")
            self._sync_art(None)
            self._sync_art_clickable(None)
        else:
            self._title.set_label(track.title)
            artist = track.artist_name
            album = track.album_title or ""
            duration = format_duration(track.duration_sec)
            self._subtitle.set_label(
                join_detail(artist, album or None, duration)
            )
            self._quality.set_label(
                join_detail(source_label(track.source), state.quality_hint)
            )
            icon = (
                "media-playback-pause-symbolic"
                if state.is_playing
                else "media-playback-start-symbolic"
            )
            self._play_btn.set_icon_name(icon)
            self._play_btn.set_tooltip_text("Pause" if state.is_playing else "Play")
            self._sync_art(track)
            self._sync_art_clickable(track)

        volume_visible = state.volume_mode != "fixed"
        self._volume_box.set_visible(volume_visible)
        self._controls_leading.set_hexpand(not volume_visible)
        if volume_visible and not self._volume_dragging and event in (
            None,
            "volume_changed",
            "playback_changed",
        ):
            self._volume.handler_block_by_func(self._on_volume_changed)
            self._volume.set_value(state.volume)
            self._volume.handler_unblock_by_func(self._on_volume_changed)
            self._sync_mute_button(state)

        return False

    def _sync_art(self, track: object | None) -> None:
        if self._art_loader is None:
            return
        if track is None:
            self._art_track_id = None
            self._art_loader.set_picture(self._art_picture, None, pixel_size=_ART_PIXEL_SIZE)
            return
        track_id = track.id if hasattr(track, "id") else None
        art_uri = track.art_uri if hasattr(track, "art_uri") else None
        if track_id == self._art_track_id:
            return
        self._art_track_id = track_id
        self._art_loader.set_picture(self._art_picture, art_uri, pixel_size=_ART_PIXEL_SIZE)

    def _reset_progress_display(self) -> None:
        self._progress_track_id = None
        self._shown_sec = 0.0
        self._shown_anchor_sec = 0.0
        self._shown_anchor_at = None
        self._set_progress_fraction(0.0, allow_decrease=True)
        self._progress.set_sensitive(False)
        self._update_seek_labels(0.0, 1.0)

    def _sync_shown_position(
        self,
        *,
        track_id: str | None,
        reported_sec: float,
        duration_sec: float,
        is_playing: bool,
    ) -> float:
        if track_id != self._progress_track_id:
            self._progress_track_id = track_id
            self._shown_sec = 0.0
            self._shown_anchor_sec = 0.0
            self._shown_anchor_at = None
            self._set_progress_fraction(0.0, allow_decrease=True)

        reported_sec = max(0.0, min(reported_sec, duration_sec))
        now = time.monotonic()
        if is_playing:
            if (
                self._shown_anchor_at is None
                or reported_sec < self._shown_anchor_sec - 0.5
            ):
                self._shown_anchor_sec = reported_sec
                self._shown_anchor_at = now
            elif reported_sec > self._shown_anchor_sec + 0.05:
                self._shown_anchor_sec = reported_sec
                self._shown_anchor_at = now
            shown = min(
                duration_sec,
                self._shown_anchor_sec + (now - self._shown_anchor_at),
            )
            if reported_sec > shown:
                shown = reported_sec
                self._shown_anchor_sec = reported_sec
                self._shown_anchor_at = now
            self._shown_sec = max(self._shown_sec, shown)
        else:
            self._shown_sec = reported_sec
            self._shown_anchor_at = None
        return self._shown_sec

    def _tick_progress(self) -> bool:
        if self._seeking:
            return True
        self._service.refresh_playback_position_for_ui()
        state = self._service.get_playback_state()
        track = state.current_track
        if track is None:
            if self._progress_track_id is not None:
                self._reset_progress_display()
            return True

        duration = self._playback_duration(state)
        if duration is None:
            if self._progress.get_sensitive():
                self._progress.set_sensitive(False)
            return True

        if not self._progress.get_sensitive():
            self._progress.set_sensitive(True)

        track_id = track.id if hasattr(track, "id") else None
        position_sec = self._sync_shown_position(
            track_id=track_id,
            reported_sec=state.position_sec,
            duration_sec=duration,
            is_playing=state.is_playing,
        )
        self._set_progress_fraction(position_sec / duration)
        self._update_seek_labels(position_sec, duration)
        return True


def attach_media_keys(window: Gtk.Widget, service: PlayerService) -> None:
    controller = Gtk.EventControllerKey.new()
    controller.connect(
        "key-pressed",
        lambda _ctrl, _n, keyval, _state: _handle_media_key(keyval, service),
    )
    window.add_controller(controller)


def _handle_media_key(keyval: int, service: PlayerService) -> bool:
    if keyval in _KEY_PLAY_PAUSE:
        service.toggle_play_pause()
        return True
    if keyval == _KEY_NEXT:
        service.skip_next()
        return True
    if keyval == _KEY_PREV:
        service.skip_previous()
        return True
    if keyval == _KEY_STOP:
        service.pause()
        return True
    if keyval == _KEY_VOLUME_UP:
        if service.volume_adjustable():
            service.adjust_volume(0.05)
        return True
    if keyval == _KEY_VOLUME_DOWN:
        if service.volume_adjustable():
            service.adjust_volume(-0.05)
        return True
    return False
