"""Release grid and detail views."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from tunes_player.core.models import Release, ReleaseCompleteness, Source, Track
from tunes_player.core.services import PlayerService
from tunes_player.ui.gtk.album_grid import (
    ALBUM_GRID_SPACING,
    ALBUM_GRID_VIEW_MARGIN,
    album_grid_content_inner_width,
    album_grid_layout,
    album_grid_min_content_width,
    album_grid_resolve_inner_width,
)
from tunes_player.ui.gtk.art import ArtLoader
from tunes_player.ui.gtk.util import (
    escape_markup,
    format_duration,
    format_track_number,
    join_detail,
    source_label,
)

_ALBUM_TILE_ART_PIXELS = 512
_ALBUM_TILE_ART_PIXELS_SMALL = 384
_ALBUM_DETAIL_ART_MIN = 220
_ALBUM_TILE_DEFAULT_EDGE = 200
_RELEASE_ART_PLAY_SIZE_RATIO = 0.30
_RELEASE_ART_PLAY_INSET_RATIO = 0.036
_RELEASE_ART_PLAY_MIN_SIZE = 36
_RELEASE_ART_PLAY_MAX_SIZE = 66


class PlaceholderView(Gtk.Box):
    def __init__(self, *, title: str, message: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.add_css_class("view")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, vexpand=True)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)
        self.append(box)

        heading = Gtk.Label(label=title)
        heading.add_css_class("title-2")
        box.append(heading)

        label = Gtk.Label(label=message, justify=Gtk.Justification.CENTER)
        label.add_css_class("dim-label")
        label.set_wrap(True)
        label.set_max_width_chars(52)
        box.append(label)


class LoadingDiscoverView(Gtk.Box):
    def __init__(self, *, message: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.add_css_class("view")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, vexpand=True)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)
        self.append(box)

        self._spinner = Gtk.Spinner()
        self._spinner.set_halign(Gtk.Align.CENTER)
        box.append(self._spinner)

        label = Gtk.Label(label=message, justify=Gtk.Justification.CENTER)
        label.add_css_class("dim-label")
        label.set_wrap(True)
        label.set_max_width_chars(52)
        box.append(label)

        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)

    def _on_map(self, *_args: object) -> None:
        self._spinner.start()

    def _on_unmap(self, *_args: object) -> None:
        self._spinner.stop()


def _release_art_play_layout(art_size: int) -> tuple[int, int]:
    """Return circular play button diameter and corner inset for *art_size* px artwork."""
    if art_size < 1:
        art_size = _ALBUM_TILE_DEFAULT_EDGE
    button = round(art_size * _RELEASE_ART_PLAY_SIZE_RATIO)
    button = max(_RELEASE_ART_PLAY_MIN_SIZE, min(_RELEASE_ART_PLAY_MAX_SIZE, button))
    inset = max(4, round(art_size * _RELEASE_ART_PLAY_INSET_RATIO))
    return button, inset


def _release_grid_playable(release: Release) -> bool:
    """Whether the grid overlay play button should be sensitive.

    Detail view loads tracks and uses ``bool(tracks)``. Grid tiles only have
    catalog metadata; streaming providers often leave ``track_count`` at 0 on
    sparse album objects even when ``get_release_tracks`` succeeds later.
    """
    if release.track_count > 0:
        return True
    return release.source != Source.LOCAL


def _track_count_label(count: int) -> str:
    noun = "track" if count == 1 else "tracks"
    return f"{count} {noun}"


def _format_release_track_count(release: Release) -> str | None:
    if release.expected_track_count and release.track_count < release.expected_track_count:
        return f"{release.track_count} / {release.expected_track_count} tracks"
    if release.track_count:
        return _track_count_label(release.track_count)
    return None


def _sync_release_art_play_button(
    btn: Gtk.Button,
    *,
    service: PlayerService,
    release_id: str,
) -> None:
    if service.is_release_playing(release_id):
        btn.set_icon_name("media-playback-pause-symbolic")
        btn.set_tooltip_text("Pause")
    else:
        btn.set_icon_name("media-playback-start-symbolic")
        btn.set_tooltip_text("Play release")


def _release_completeness_label(release: Release) -> str | None:
    if release.completeness == ReleaseCompleteness.PARTIAL:
        expected = release.expected_track_count or "?"
        return f"Partial — {release.track_count} of {expected} tracks"
    if release.completeness == ReleaseCompleteness.SYNTHETIC:
        return "Synthetic release"
    return None


class ReleaseGridView(Gtk.ScrolledWindow):
    def __init__(
        self,
        *,
        releases: list[Release],
        on_release_activated: Callable[[str], None],
        on_release_play: Callable[[str], None],
        on_artist_search: Callable[[str], None] | None = None,
        empty_message: str | None = None,
        art_loader: ArtLoader | None = None,
        window_inner_width_fn: Callable[[], int] | None = None,
        service: PlayerService | None = None,
    ) -> None:
        super().__init__(vexpand=True, hscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self.set_propagate_natural_width(False)
        self.add_css_class("view")
        self._window_inner_width_fn = window_inner_width_fn
        self._last_viewport_inner = 0
        self._last_window_inner = 0
        self._root_width_notify_id = 0

        if not releases:
            label = Gtk.Label(
                label=empty_message or "No releases to show.",
                vexpand=True,
                justify=Gtk.Justification.CENTER,
            )
            label.add_css_class("dim-label")
            label.set_valign(Gtk.Align.CENTER)
            label.set_wrap(True)
            label.set_max_width_chars(52)
            label.set_margin_top(24)
            label.set_margin_bottom(24)
            label.set_margin_start(24)
            label.set_margin_end(24)
            self.set_child(label)
            return

        grid = ReleaseTileGrid(
            inner_width_fn=self._album_tile_inner_width,
            service=service,
        )
        grid.set_margin_top(ALBUM_GRID_VIEW_MARGIN)
        grid.set_margin_bottom(ALBUM_GRID_VIEW_MARGIN)
        grid.set_margin_start(ALBUM_GRID_VIEW_MARGIN)
        grid.set_margin_end(ALBUM_GRID_VIEW_MARGIN)
        shell = _FixedMinWidthShell(album_grid_min_content_width())
        shell.append(grid)
        self.set_child(shell)
        self._tile_grid = grid
        self.connect("notify::width", self._on_viewport_width_changed)
        self.connect("map", self._on_view_map)
        self.connect("unmap", self._on_view_unmap)

        ReleaseGridView._populate_releases(
            grid,
            releases,
            on_release_activated,
            on_release_play,
            on_artist_search,
            art_loader=art_loader,
        )

    def sync_tile_layout(self) -> None:
        grid = getattr(self, "_tile_grid", None)
        if grid is not None:
            grid.sync_layout()

    @staticmethod
    def _populate_releases(
        grid: ReleaseTileGrid,
        releases: list[Release],
        on_release_activated: Callable[[str], None],
        on_release_play: Callable[[str], None],
        on_artist_search: Callable[[str], None] | None = None,
        art_loader: ArtLoader | None = None,
        start: int = 0,
        batch_size: int = 24,
        small: bool = False,
    ) -> None:
        end = min(start + batch_size, len(releases))
        for release in releases[start:end]:
            grid.append_release(
                release,
                on_activate=lambda release_id=release.id: on_release_activated(release_id),
                on_play=lambda release_id=release.id: on_release_play(release_id),
                on_artist_search=on_artist_search,
                art_loader=art_loader,
                small=small,
            )

        if end < len(releases):
            # GLib.idle_add only forwards positional arguments to the callback.
            GLib.idle_add(
                ReleaseGridView._populate_releases,
                grid,
                releases,
                on_release_activated,
                on_release_play,
                on_artist_search,
                art_loader,
                end,
                batch_size,
                small,
            )
            GLib.idle_add(grid._sync_layout_idle)
        else:
            GLib.idle_add(grid._sync_layout_idle)

    def _viewport_inner_width(self) -> int:
        width = self.get_width()
        if width < 1:
            width = self.get_allocation().width
        if width < 1:
            return 0
        return album_grid_content_inner_width(
            width,
            margin_start=ALBUM_GRID_VIEW_MARGIN,
            margin_end=ALBUM_GRID_VIEW_MARGIN,
        )

    def _album_tile_inner_width(self) -> int:
        viewport = self._viewport_inner_width()
        window = self._window_inner_width_fn() if self._window_inner_width_fn else 0
        inner, self._last_viewport_inner, self._last_window_inner = album_grid_resolve_inner_width(
            viewport_inner=viewport,
            window_inner=window,
            last_viewport_inner=self._last_viewport_inner,
            last_window_inner=self._last_window_inner,
        )
        return inner

    def _on_view_map(self, *_args: object) -> None:
        root = self.get_root()
        if root is not None and not self._root_width_notify_id:
            self._root_width_notify_id = root.connect(
                "notify::width",
                self._on_viewport_width_changed,
            )

    def _on_view_unmap(self, *_args: object) -> None:
        if self._root_width_notify_id:
            root = self.get_root()
            if root is not None:
                root.disconnect(self._root_width_notify_id)
            self._root_width_notify_id = 0

    def _on_viewport_width_changed(self, *_args: object) -> None:
        GLib.idle_add(self._tile_grid._sync_layout_idle)


class QueueSheet(Adw.Dialog):
    def __init__(self, *, service: PlayerService) -> None:
        super().__init__()
        self._service = service
        self.set_title("Queue")
        self.set_content_width(420)
        self.set_content_height(480)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)
        self.set_child(toolbar)

        scrolled = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        toolbar.set_content(scrolled)

        self._list_box = Gtk.ListBox()
        self._list_box.add_css_class("boxed-list")
        self._list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled.set_child(self._list_box)

        service.subscribe(lambda _event: GLib.idle_add(self._refresh_on_main))

        self._refresh()

    def _refresh_on_main(self) -> bool:
        self._refresh()
        return False

    def _refresh(self) -> None:
        child = self._list_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._list_box.remove(child)
            child = next_child

        state = self._service.get_playback_state()
        if not state.queue:
            row = Adw.ActionRow(title="Queue is empty", subtitle="Play an album or track")
            row.set_sensitive(False)
            self._list_box.append(row)
            return

        for index, track in enumerate(state.queue):
            row = Adw.ActionRow(
                title=escape_markup(track.title),
                subtitle=escape_markup(
                    join_detail(
                        source_label(track.source),
                        track.artist_name,
                        track.album_title or None,
                    )
                ),
            )
            if index == state.queue_index:
                row.add_prefix(Gtk.Image.new_from_icon_name("media-playback-start-symbolic"))
            row.set_activatable(True)
            row.connect(
                "activated",
                lambda _row, track_id=track.id: self._service.play_track(track_id),
            )
            self._list_box.append(row)


class ReleaseDetailView(Gtk.Box):
    def __init__(
        self,
        *,
        service: PlayerService,
        release: Release,
        art_loader: ArtLoader | None = None,
        on_artist_search: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.add_css_class("view")
        self.add_css_class("release-detail-view")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        header.add_css_class("release-detail-header")
        header.set_vexpand(False)
        header.set_hexpand(True)
        self.append(header)

        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        header_row.set_halign(Gtk.Align.START)
        header_row.set_valign(Gtk.Align.FILL)
        header_row.set_hexpand(True)
        header_row.set_vexpand(False)
        header.append(header_row)

        tracks = service.get_release_tracks(release.id)

        art_frame = _square_art_with_play(
            release,
            size=_ALBUM_DETAIL_ART_MIN,
            art_loader=art_loader,
            css_class="album-detail-art",
            on_play=lambda: service.play_or_toggle_release(release.id, start_index=0),
            playable=bool(tracks),
            fill_cell=True,
        )
        setattr(art_frame, "_tunes_release_id", release.id)
        art_frame.set_valign(Gtk.Align.FILL)
        header_row.append(art_frame)
        self._detail_service = service
        self._detail_release_id = release.id
        self._detail_art_frame = art_frame
        self._playback_unsubscribe = service.subscribe(self._on_playback_event)
        self.connect("destroy", self._on_destroy)
        GLib.idle_add(self._sync_release_art_play, service, release.id, art_frame)

        details_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        details_column.set_hexpand(False)
        details_column.set_vexpand(False)
        details_column.set_halign(Gtk.Align.START)
        details_column.set_valign(Gtk.Align.CENTER)
        details_column.set_margin_top(12)
        details_column.set_margin_bottom(12)
        details_column.set_margin_end(18)
        header_row.append(details_column)

        _bind_detail_hero_art_sync(
            header_row,
            art_frame,
            details_column,
            release=release,
            art_loader=art_loader,
            min_edge=_ALBUM_DETAIL_ART_MIN,
        )

        details_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        details_text.set_halign(Gtk.Align.START)
        details_column.append(details_text)

        title = Gtk.Label(label=release.title, xalign=0.0, ellipsize=3)
        title.add_css_class("title-1")
        title.set_wrap(False)
        title.set_halign(Gtk.Align.START)
        details_text.append(title)

        details_text.append(
            _detail_artist_year_row(release, on_artist_search=on_artist_search)
        )

        duration = format_duration(release.duration_sec)
        track_count = _format_release_track_count(release)
        info = Gtk.Label(
            label=join_detail(
                track_count,
                duration,
                release.genre,
                source_label(release.source),
            ),
            xalign=0.0,
            ellipsize=3,
        )
        info.add_css_class("dim-label")
        info.set_wrap(False)
        info.set_halign(Gtk.Align.START)
        details_text.append(info)

        completeness = _release_completeness_label(release)
        if completeness:
            status = Gtk.Label(label=completeness, xalign=0.0, ellipsize=3)
            status.add_css_class("dim-label")
            status.set_halign(Gtk.Align.START)
            details_text.append(status)

        scrolled = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scrolled.set_margin_top(0)
        self.append(scrolled)

        list_box = Gtk.ListBox()
        list_box.add_css_class("boxed-list")
        list_box.add_css_class("release-detail-tracks")
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        list_box.set_valign(Gtk.Align.START)
        list_box.set_vexpand(False)
        list_box.connect(
            "row-activated",
            lambda _box, row: service.play_track(row.track_id),
        )
        scrolled.set_child(list_box)

        for index, track in enumerate(tracks):
            list_box.append(_compact_track_row(track, index=index))

    def _on_playback_event(self, event: str) -> None:
        if event == "position_changed":
            return
        GLib.idle_add(self._sync_release_art_play_idle)

    def _sync_release_art_play_idle(self) -> bool:
        self._sync_release_art_play(
            self._detail_service,
            self._detail_release_id,
            self._detail_art_frame,
        )
        return False

    @staticmethod
    def _sync_release_art_play(
        service: PlayerService,
        release_id: str,
        art_frame: Gtk.Widget,
    ) -> bool:
        btn = _find_release_art_play_button(art_frame)
        if btn is not None:
            _sync_release_art_play_button(btn, service=service, release_id=release_id)
        return False

    def _on_destroy(self, *_args: object) -> None:
        unsubscribe = getattr(self, "_playback_unsubscribe", None)
        if unsubscribe is not None:
            unsubscribe()


def _compact_track_row(track: Track, *, index: int) -> Gtk.ListBoxRow:
    row = Gtk.ListBoxRow()
    row.track_id = track.id
    row.set_activatable(True)

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    box.set_margin_top(4)
    box.set_margin_bottom(4)
    box.set_margin_start(12)
    box.set_margin_end(12)
    row.set_child(box)

    number = format_track_number(track, fallback=index + 1) or str(index + 1)
    num_label = Gtk.Label(label=number, xalign=1.0)
    num_label.set_width_chars(4)
    num_label.add_css_class("dim-label")
    num_label.add_css_class("numeric")
    num_label.set_valign(Gtk.Align.CENTER)
    box.append(num_label)

    title = Gtk.Label(label=track.title, xalign=0.0, ellipsize=3)
    title.set_hexpand(True)
    title.set_halign(Gtk.Align.START)
    title.set_valign(Gtk.Align.CENTER)
    box.append(title)

    meta = Gtk.Label(
        label=join_detail(
            format_duration(track.duration_sec),
            source_label(track.source),
        ),
        xalign=1.0,
    )
    meta.add_css_class("dim-label")
    meta.add_css_class("caption")
    meta.set_halign(Gtk.Align.END)
    meta.set_valign(Gtk.Align.CENTER)
    box.append(meta)

    return row


def _track_row(
    track: Track,
    *,
    index: int | None = None,
    on_activate: Callable[[], None] | None = None,
) -> Gtk.ListBoxRow:
    row = Gtk.ListBoxRow()
    row.set_activatable(on_activate is not None)
    if on_activate is not None:
        row.connect("activated", lambda *_args: on_activate())

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    box.set_margin_top(8)
    box.set_margin_bottom(8)
    box.set_margin_start(12)
    box.set_margin_end(12)
    row.set_child(box)

    if index is not None:
        number = Gtk.Label(label=format_track_number(index), xalign=1.0)
        number.add_css_class("dim-label")
        number.set_width_chars(3)
        box.append(number)

    details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    details.set_hexpand(True)
    box.append(details)

    title = Gtk.Label(label=track.title, xalign=0)
    title.set_halign(Gtk.Align.START)
    title.set_ellipsize(3)
    details.append(title)

    subtitle = Gtk.Label(
        label=join_detail(track.artist_name, track.album_title),
        xalign=0,
    )
    subtitle.add_css_class("dim-label")
    subtitle.set_halign(Gtk.Align.START)
    subtitle.set_ellipsize(3)
    details.append(subtitle)

    meta = Gtk.Label(
        label=join_detail(
            format_duration(track.duration_sec),
            source_label(track.source),
        ),
        xalign=1.0,
    )
    meta.add_css_class("dim-label")
    meta.add_css_class("caption")
    meta.set_halign(Gtk.Align.END)
    meta.set_valign(Gtk.Align.CENTER)
    box.append(meta)

    return row


def _section_label(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.add_css_class("heading")
    return label


class _FixedMinWidthShell(Gtk.Box):
    """Scroll child shell: fixed horizontal minimum so the window can shrink to one column."""

    def __init__(self, min_width: int) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._min_width = min_width
        self.set_hexpand(True)
        self.set_halign(Gtk.Align.FILL)

    def do_measure(  # noqa: N802 — GTK vfunc
        self,
        orientation: Gtk.Orientation,
        for_size: int,
    ) -> tuple[int, int, int, int]:
        if orientation == Gtk.Orientation.HORIZONTAL:
            return self._min_width, self._min_width, -1, -1
        return Gtk.Box.do_measure(self, orientation, for_size)


class ReleaseTileGrid(Gtk.Grid):
    """Square release tiles; column count follows this widget's allocated width."""

    def __init__(
        self,
        *,
        inner_width_fn: Callable[[], int] | None = None,
        service: PlayerService | None = None,
    ) -> None:
        super().__init__()
        self.set_column_spacing(ALBUM_GRID_SPACING)
        self.set_row_spacing(ALBUM_GRID_SPACING)
        self.set_column_homogeneous(False)
        self.set_row_homogeneous(False)
        self.add_css_class("album-tile-grid")
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)
        self.set_hexpand(True)
        self.set_vexpand(False)
        self._inner_width_fn = inner_width_fn
        self._service = service
        self._playback_unsubscribe: Callable[[], None] | None = None
        self._last_art_playing_release_id: str | None = None
        if service is not None:
            self._playback_unsubscribe = service.subscribe(self._on_playback_event)
            self.connect("destroy", self._on_destroy)
        self._cards: list[Gtk.Widget] = []
        self._tile_edge = _ALBUM_TILE_DEFAULT_EDGE
        self._layout_key: tuple[int, int, int, int] | None = None
        self._in_relayout = False
        self._tick_callback_id = 0
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)
        self.connect("notify::width", self._on_width_changed)

    def _on_map(self, *_args: object) -> None:
        if not self._tick_callback_id:
            self._tick_callback_id = self.add_tick_callback(self._on_tick)
        GLib.idle_add(self._sync_layout_idle)

    def _on_width_changed(self, *_args: object) -> None:
        if self._in_relayout:
            return
        GLib.idle_add(self._sync_layout_idle)

    def _sync_layout_idle(self) -> bool:
        self.sync_layout()
        return False

    def sync_layout(self) -> None:
        inner = self._available_inner_width()
        if inner > 0 and self._cards:
            self.relayout(inner)

    def _available_inner_width(self) -> int:
        if self._inner_width_fn is not None:
            inner = self._inner_width_fn()
            if inner > 0:
                return inner
        width = self.get_width()
        if width >= 1:
            return width
        alloc_w = self.get_allocation().width
        if alloc_w >= 1:
            return alloc_w
        return 0

    def _on_unmap(self, *_args: object) -> None:
        if self._tick_callback_id:
            self.remove_tick_callback(self._tick_callback_id)
            self._tick_callback_id = 0

    def _on_tick(self, _widget: Gtk.Widget, _frame_clock: object) -> bool:
        if self._in_relayout or not self._cards:
            return True
        inner = self._available_inner_width()
        if inner < 1:
            return True
        columns, edge = album_grid_layout(inner)
        key = (inner, columns, edge, len(self._cards))
        if key != self._layout_key:
            self.relayout(inner)
        return True

    def append_release(
        self,
        release: Release,
        *,
        on_activate: Callable[[], None],
        on_play: Callable[[], None],
        on_artist_search: Callable[[str], None] | None = None,
        art_loader: ArtLoader | None,
        small: bool = False,
    ) -> None:
        card = _release_card(
            release,
            on_play=on_play,
            on_artist_search=on_artist_search,
            art_loader=art_loader,
            small=small,
            edge=self._tile_edge,
        )
        _attach_album_card_activate(card, on_activate)
        self._cards.append(card)
        if self._service is not None:
            release_id = getattr(card, "_tunes_release_id", None)
            if isinstance(release_id, str):
                btn = _find_release_art_play_button(card)
                if btn is not None:
                    _sync_release_art_play_button(
                        btn,
                        service=self._service,
                        release_id=release_id,
                    )

    def _on_playback_event(self, event: str) -> None:
        if event == "position_changed" or self._service is None:
            return
        GLib.idle_add(self._sync_art_play_buttons_idle)

    def _sync_art_play_buttons_idle(self) -> bool:
        service = self._service
        if service is None:
            return False
        state = service.get_playback_state()
        playing_id = service.current_release_id() if state.is_playing else None
        to_update: set[str] = set()
        if self._last_art_playing_release_id:
            to_update.add(self._last_art_playing_release_id)
        if playing_id:
            to_update.add(playing_id)
        self._last_art_playing_release_id = playing_id
        if not to_update:
            return False
        for card in self._cards:
            release_id = getattr(card, "_tunes_release_id", None)
            if not isinstance(release_id, str) or release_id not in to_update:
                continue
            btn = _find_release_art_play_button(card)
            if btn is not None:
                _sync_release_art_play_button(
                    btn,
                    service=service,
                    release_id=release_id,
                )
        return False

    def _on_destroy(self, *_args: object) -> None:
        if self._playback_unsubscribe is not None:
            self._playback_unsubscribe()
            self._playback_unsubscribe = None

    def relayout(self, inner_width: int) -> None:
        if not self._cards or inner_width < 1:
            return

        columns, edge = album_grid_layout(inner_width)
        layout_key = (inner_width, columns, edge, len(self._cards))
        if layout_key == self._layout_key:
            return

        self._in_relayout = True
        try:
            self._layout_key = layout_key
            self._tile_edge = edge
            self._detach_cards()
            self._clear_rows()
            for card in self._cards:
                _reset_album_tile_size(card)

            for index, card in enumerate(self._cards):
                _apply_album_tile_size(card, edge)
                row = index // columns
                column = index % columns
                self.attach(card, column, row, 1, 1)
        finally:
            self._in_relayout = False

    def _detach_cards(self) -> None:
        for card in self._cards:
            parent = card.get_parent()
            if parent is not None:
                parent.remove(card)

    def _clear_rows(self) -> None:
        child = self.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.remove(child)
            child = next_child


def _reset_album_tile_size(card: Gtk.Widget) -> None:
    card.set_size_request(-1, -1)


def _find_art_picture(root: Gtk.Widget) -> Gtk.Picture | None:
    if isinstance(root, Gtk.Picture):
        return root
    child = root.get_first_child()
    while child is not None:
        found = _find_art_picture(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def _bind_detail_hero_art_sync(
    header_row: Gtk.Box,
    art_frame: Gtk.Widget,
    details_column: Gtk.Box,
    *,
    release: Release,
    art_loader: ArtLoader | None,
    min_edge: int,
) -> None:
    """Keep detail cover square and flush with the hero header row height."""
    state: dict[str, int] = {"edge": 0, "pixel_size": 0}

    def sync(*_args: object) -> None:
        _sync_detail_hero_art(
            header_row,
            art_frame,
            details_column,
            release=release,
            art_loader=art_loader,
            min_edge=min_edge,
            state=state,
        )

    for widget in (header_row, details_column, art_frame):
        widget.connect("notify::height", sync)
    header_row.connect("map", sync)
    GLib.idle_add(sync)


def _sync_detail_hero_art(
    header_row: Gtk.Box,
    art_frame: Gtk.Widget,
    details_column: Gtk.Box,
    *,
    release: Release,
    art_loader: ArtLoader | None,
    min_edge: int,
    state: dict[str, int],
) -> None:
    row_h = header_row.get_allocated_height()
    if row_h < 1:
        _min_h, row_h, _min_b, _nat_b = header_row.measure(Gtk.Orientation.VERTICAL, -1)
    if row_h < 1:
        _min_h, row_h, _min_b, _nat_b = details_column.measure(Gtk.Orientation.VERTICAL, -1)
    edge = max(min_edge, row_h)
    if edge == state.get("edge"):
        return
    state["edge"] = edge
    _apply_album_tile_size(art_frame, edge)
    last_px = state.get("pixel_size", 0)
    if art_loader is None or edge <= last_px:
        return
    picture = _find_art_picture(art_frame)
    if picture is None:
        return
    state["pixel_size"] = edge
    art_loader.set_picture(picture, release.art_uri, pixel_size=edge)


def _find_release_art_play_button(root: Gtk.Widget) -> Gtk.Button | None:
    if isinstance(root, Gtk.Button) and root.has_css_class("release-art-play"):
        return root
    child = root.get_first_child()
    while child is not None:
        found = _find_release_art_play_button(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def _apply_release_art_play_button_metrics(btn: Gtk.Button, art_size: int) -> None:
    button_px, inset = _release_art_play_layout(art_size)
    btn.set_size_request(button_px, button_px)
    btn.set_margin_start(inset)
    btn.set_margin_top(inset)


def _apply_album_tile_size(card: Gtk.Widget, edge: int) -> None:
    if edge < 1:
        return
    card.set_size_request(edge, edge)
    play_btn = _find_release_art_play_button(card)
    if play_btn is not None:
        _apply_release_art_play_button_metrics(play_btn, edge)


def _picked_has_css_class(widget: Gtk.Widget, x: float, y: float, css_class: str) -> bool:
    picked = widget.pick(x, y, Gtk.PickFlags.DEFAULT)
    while picked is not None:
        if picked.has_css_class(css_class):
            return True
        picked = picked.get_parent()
    return False


def _picked_is_release_art_play(widget: Gtk.Widget, x: float, y: float) -> bool:
    return _picked_has_css_class(widget, x, y, "release-art-play")


def _picked_is_artist_link(widget: Gtk.Widget, x: float, y: float) -> bool:
    return _picked_has_css_class(widget, x, y, "artist-link")


def _attach_album_card_activate(tile: Gtk.Widget, callback: Callable[[], None]) -> None:
    gesture = Gtk.GestureClick()

    def _on_released(_gesture: Gtk.GestureClick, _n_press: int, x: float, y: float) -> None:
        if _picked_is_release_art_play(tile, x, y) or _picked_is_artist_link(tile, x, y):
            return
        callback()

    gesture.connect("released", _on_released)
    tile.add_controller(gesture)
    tile.set_focusable(True)
    tile.set_cursor_from_name("pointer")


def _create_release_art_play_button(
    *,
    on_play: Callable[[], None],
    playable: bool,
    art_size: int,
) -> Gtk.Button:
    btn = Gtk.Button()
    btn.add_css_class("circular")
    btn.add_css_class("suggested-action")
    btn.add_css_class("release-art-play")
    btn.set_icon_name("media-playback-start-symbolic")
    btn.set_tooltip_text("Play release")
    btn.set_focusable(True)
    btn.set_sensitive(playable)
    btn.set_halign(Gtk.Align.START)
    btn.set_valign(Gtk.Align.START)
    _apply_release_art_play_button_metrics(btn, art_size)
    btn.connect("clicked", lambda *_args: on_play())
    return btn


def _attach_release_art_play(
    overlay: Gtk.Overlay,
    *,
    on_play: Callable[[], None],
    playable: bool,
    art_size: int,
) -> Gtk.Button:
    btn = _create_release_art_play_button(on_play=on_play, playable=playable, art_size=art_size)
    overlay.add_overlay(btn)
    return btn


def _square_art_with_play(
    release: Release,
    *,
    size: int,
    art_loader: ArtLoader | None,
    css_class: str = "album-card-art",
    on_play: Callable[[], None] | None = None,
    playable: bool = True,
    fill_cell: bool = False,
) -> Gtk.Widget:
    """Fixed square cover with optional top-left play overlay."""
    frame = Gtk.Box()
    frame.add_css_class(css_class)
    frame.add_css_class("release-art-shell")
    frame.set_size_request(size, size)
    frame.set_halign(Gtk.Align.FILL)
    frame.set_valign(Gtk.Align.FILL if fill_cell else Gtk.Align.START)
    frame.set_hexpand(False)
    frame.set_vexpand(False)

    overlay = Gtk.Overlay()
    overlay.set_halign(Gtk.Align.FILL)
    overlay.set_valign(Gtk.Align.FILL)
    overlay.set_hexpand(fill_cell)
    overlay.set_vexpand(fill_cell)
    frame.append(overlay)

    art = Gtk.Picture()
    art.set_halign(Gtk.Align.FILL)
    art.set_valign(Gtk.Align.FILL)
    # Grid tiles use a fixed square frame; detail hero resizes via _sync_detail_hero_art.
    art.set_hexpand(fill_cell)
    art.set_vexpand(fill_cell)
    art.set_can_shrink(True)
    art.set_content_fit(Gtk.ContentFit.COVER)
    if art_loader is not None:
        art_loader.set_picture(art, release.art_uri, pixel_size=size)
    else:
        art.set_paintable(None)
    overlay.set_child(art)

    if on_play is not None:
        _attach_release_art_play(overlay, on_play=on_play, playable=playable, art_size=size)

    return frame


def _detail_artist_year_row(
    release: Release,
    *,
    on_artist_search: Callable[[str], None] | None,
) -> Gtk.Box:
    """Detail header row: artist (optionally linked) and release year."""
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    row.set_halign(Gtk.Align.START)

    if on_artist_search is not None:
        row.append(
            _artist_name_link(
                release.artist_name,
                on_activate=lambda name=release.artist_name: on_artist_search(name),
            )
        )
    else:
        artist = Gtk.Label(label=release.artist_name, xalign=0.0, ellipsize=3)
        artist.add_css_class("title-4")
        artist.add_css_class("dim-label")
        artist.set_wrap(False)
        artist.set_halign(Gtk.Align.START)
        row.append(artist)

    year = str(release.year) if release.year else None
    if year:
        year_label = Gtk.Label(label=f" · {year}", xalign=0.0, ellipsize=3)
        year_label.add_css_class("title-4")
        year_label.add_css_class("dim-label")
        year_label.set_wrap(False)
        year_label.set_halign(Gtk.Align.START)
        row.append(year_label)
    return row


def _artist_name_link(name: str, *, on_activate: Callable[[], None]) -> Gtk.Button:
    label = Gtk.Label(label=name, xalign=0.0, ellipsize=3)
    label.add_css_class("title-4")
    label.add_css_class("dim-label")
    label.set_wrap(False)
    label.set_halign(Gtk.Align.START)

    button = Gtk.Button()
    button.add_css_class("flat")
    button.add_css_class("artist-link")
    button.set_child(label)
    button.set_halign(Gtk.Align.START)
    button.set_tooltip_text("Search for releases by this artist")
    button.set_cursor_from_name("pointer")
    button.connect("clicked", lambda *_args: on_activate())
    return button


def _overlay_label(text: str, *, extra_classes: tuple[str, ...] = ()) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0.0, ellipsize=3)
    label.set_halign(Gtk.Align.START)
    label.set_wrap(False)
    for css_class in extra_classes:
        label.add_css_class(css_class)
    return label


def _overlay_artist_link(name: str, *, on_activate: Callable[[], None]) -> Gtk.Button:
    label = Gtk.Label(label=name, xalign=0.0, ellipsize=3)
    label.add_css_class("album-card-meta")
    label.set_halign(Gtk.Align.START)
    label.set_wrap(False)

    button = Gtk.Button()
    button.add_css_class("flat")
    button.add_css_class("artist-link")
    button.set_child(label)
    button.set_halign(Gtk.Align.START)
    button.set_tooltip_text("Search for releases by this artist")
    button.set_cursor_from_name("pointer")
    button.connect("clicked", lambda *_args: on_activate())
    return button


def _overlay_artist_year_row(
    release: Release,
    *,
    on_artist_search: Callable[[str], None] | None,
) -> Gtk.Box:
    """Grid tile overlay: artist (optionally linked) and release year."""
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    row.set_halign(Gtk.Align.START)

    if on_artist_search is not None:
        row.append(
            _overlay_artist_link(
                release.artist_name,
                on_activate=lambda name=release.artist_name: on_artist_search(name),
            )
        )
    else:
        row.append(_overlay_label(release.artist_name, extra_classes=("album-card-meta",)))

    year = str(release.year) if release.year else None
    if year:
        row.append(_overlay_label(f" · {year}", extra_classes=("album-card-meta",)))
    return row


def _release_card(
    release: Release,
    *,
    on_play: Callable[[], None],
    on_artist_search: Callable[[str], None] | None = None,
    small: bool = False,
    art_loader: ArtLoader | None = None,
    edge: int = _ALBUM_TILE_DEFAULT_EDGE,
) -> Gtk.Widget:
    """Square tile: cover fills the cell; title/artist/source overlaid at the bottom."""
    shell = Gtk.Box()
    shell.add_css_class("album-card-shell")
    shell.set_size_request(edge, edge)
    shell.set_hexpand(False)
    shell.set_vexpand(False)
    shell.set_halign(Gtk.Align.START)
    shell.set_valign(Gtk.Align.START)
    setattr(shell, "_tunes_release_id", release.id)

    frame = Gtk.AspectFrame()
    frame.set_ratio(1.0)
    frame.set_obey_child(False)
    frame.add_css_class("album-card-frame")
    frame.set_hexpand(False)
    frame.set_vexpand(False)
    frame.set_halign(Gtk.Align.FILL)
    frame.set_valign(Gtk.Align.FILL)
    shell.append(frame)

    tile = Gtk.Box()
    tile.add_css_class("card")
    tile.add_css_class("album-card")
    frame.set_child(tile)

    overlay = Gtk.Overlay()
    overlay.set_hexpand(True)
    overlay.set_vexpand(True)
    tile.append(overlay)

    picture = Gtk.Picture()
    picture.add_css_class("album-card-art")
    picture.set_content_fit(Gtk.ContentFit.COVER)
    picture.set_can_shrink(True)
    picture.set_hexpand(True)
    picture.set_vexpand(True)
    overlay.set_child(picture)

    load_pixels = _ALBUM_TILE_ART_PIXELS_SMALL if small else _ALBUM_TILE_ART_PIXELS
    if art_loader is not None:
        art_loader.set_picture(picture, release.art_uri, pixel_size=load_pixels)

    _attach_release_art_play(
        overlay,
        on_play=on_play,
        playable=_release_grid_playable(release),
        art_size=edge,
    )

    labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    labels.add_css_class("album-card-overlay")
    labels.set_valign(Gtk.Align.END)
    labels.set_halign(Gtk.Align.FILL)
    labels.set_hexpand(True)
    labels.set_vexpand(True)
    overlay.add_overlay(labels)

    labels.append(_overlay_label(release.title, extra_classes=("album-card-title",)))
    labels.append(_overlay_artist_year_row(release, on_artist_search=on_artist_search))
    meta = join_detail(
        _format_release_track_count(release),
        release.genre,
        source_label(release.source),
    )
    labels.append(_overlay_label(meta, extra_classes=("album-card-meta",)))

    return shell


# Backward-compatible aliases.
AlbumGridView = ReleaseGridView
AlbumDetailView = ReleaseDetailView
AlbumTileGrid = ReleaseTileGrid
RecentlyAddedListView = ReleaseGridView
