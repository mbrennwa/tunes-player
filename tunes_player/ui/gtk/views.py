"""Browse and detail views."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from tunes_player.core.models import Album, Artist, Track
from tunes_player.core.services import PlayerService
from tunes_player.ui.gtk.album_grid import (
    ALBUM_GRID_SPACING,
    ALBUM_GRID_VIEW_MARGIN,
    SEARCH_VIEW_HORIZONTAL_MARGIN,
    album_grid_content_inner_width,
    album_grid_layout,
    album_grid_min_content_width,
    album_grid_resolve_inner_width,
    search_grid_min_content_width,
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
_ALBUM_DETAIL_ART_SIZE = 220
_ALBUM_TILE_DEFAULT_EDGE = 200


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


class AlbumGridView(Gtk.ScrolledWindow):
    def __init__(
        self,
        *,
        albums: list[Album],
        on_album_activated: Callable[[str], None],
        empty_message: str | None = None,
        art_loader: ArtLoader | None = None,
        window_inner_width_fn: Callable[[], int] | None = None,
    ) -> None:
        super().__init__(vexpand=True, hscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self.set_propagate_natural_width(False)
        self.add_css_class("view")
        self._window_inner_width_fn = window_inner_width_fn
        self._last_viewport_inner = 0
        self._last_window_inner = 0
        self._root_width_notify_id = 0

        if not albums and empty_message:
            label = Gtk.Label(label=empty_message, vexpand=True, justify=Gtk.Justification.CENTER)
            label.add_css_class("dim-label")
            label.set_valign(Gtk.Align.CENTER)
            label.set_margin_top(24)
            label.set_margin_bottom(24)
            label.set_margin_start(24)
            label.set_margin_end(24)
            self.set_child(label)
            return

        grid = AlbumTileGrid(inner_width_fn=self._album_tile_inner_width)
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

        AlbumGridView._populate_albums(
            grid,
            albums,
            on_album_activated,
            art_loader=art_loader,
        )

    @staticmethod
    def _populate_albums(
        grid: AlbumTileGrid,
        albums: list[Album],
        on_album_activated: Callable[[str], None],
        *,
        art_loader: ArtLoader | None = None,
        start: int = 0,
        batch_size: int = 24,
        small: bool = False,
    ) -> None:
        end = min(start + batch_size, len(albums))
        for album in albums[start:end]:
            grid.append_album(
                album,
                on_activate=lambda album_id=album.id: on_album_activated(album_id),
                art_loader=art_loader,
                small=small,
            )

        if end < len(albums):
            GLib.idle_add(
                AlbumGridView._populate_albums,
                grid,
                albums,
                on_album_activated,
                art_loader,
                start=end,
                batch_size=batch_size,
                small=small,
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


class ArtistListView(Gtk.ScrolledWindow):
    def __init__(
        self,
        *,
        artists: list[Artist],
        on_artist_activated: Callable[[str], None],
        empty_message: str | None = None,
    ) -> None:
        super().__init__(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.set_propagate_natural_width(False)
        self.add_css_class("view")

        if not artists and empty_message:
            label = Gtk.Label(label=empty_message, vexpand=True, justify=Gtk.Justification.CENTER)
            label.add_css_class("dim-label")
            label.set_valign(Gtk.Align.CENTER)
            self.set_child(label)
            return

        list_box = Gtk.ListBox()
        list_box.add_css_class("navigation-sidebar")
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.set_child(list_box)

        for artist in artists:
            row = Adw.ActionRow(title=artist.name)
            row.set_activatable(True)
            row.connect("activated", lambda _row, aid=artist.id: on_artist_activated(aid))
            list_box.append(row)


class SearchResultsView(Gtk.ScrolledWindow):
    def __init__(
        self,
        *,
        service: PlayerService,
        query: str,
        on_album_activated: Callable[[str], None],
        art_loader: ArtLoader | None = None,
        window_inner_width_fn: Callable[[], int] | None = None,
    ) -> None:
        super().__init__(vexpand=True, hscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self.set_propagate_natural_width(False)
        self.add_css_class("view")
        self._window_inner_width_fn = window_inner_width_fn
        self._last_viewport_inner = 0
        self._last_window_inner = 0
        self._root_width_notify_id = 0

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, vexpand=True)
        box.set_hexpand(True)
        box.set_halign(Gtk.Align.FILL)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        self.set_child(box)
        self._results_box = box
        self.connect("notify::width", self._on_viewport_width_changed)
        self.connect("map", self._on_view_map)
        self.connect("unmap", self._on_view_unmap)

        results = service.search(query)
        if not results.albums and not results.tracks:
            empty = Gtk.Label(label=f'No results for “{query}”', vexpand=True)
            empty.add_css_class("dim-label")
            box.append(empty)
            return

        if results.albums:
            box.append(_section_label("Albums"))
            album_grid = AlbumTileGrid(inner_width_fn=self._search_album_tile_inner_width)
            album_grid.set_hexpand(True)
            album_grid.set_halign(Gtk.Align.FILL)
            album_shell = _FixedMinWidthShell(search_grid_min_content_width())
            album_shell.append(album_grid)
            box.append(album_shell)
            self._search_album_grid = album_grid
            for album in results.albums:
                album_grid.append_album(
                    album,
                    on_activate=lambda album_id=album.id: on_album_activated(album_id),
                    art_loader=art_loader,
                    small=True,
                )
            GLib.idle_add(album_grid._sync_layout_idle)

        if results.tracks:
            box.append(_section_label("Tracks"))
            track_list = Gtk.ListBox()
            track_list.add_css_class("boxed-list")
            track_list.set_selection_mode(Gtk.SelectionMode.NONE)
            box.append(track_list)
            for track in results.tracks:
                detail = join_detail(track.artist_name, track.album_title or None)
                subtitle = join_detail(source_label(track.source), detail)
                row = Adw.ActionRow(
                    title=escape_markup(track.title),
                    subtitle=escape_markup(subtitle),
                )
                row.set_activatable(True)
                row.connect(
                    "activated",
                    lambda _row, track_id=track.id: service.play_track(track_id),
                )
                track_list.append(row)

    def _viewport_inner_width(self) -> int:
        width = self.get_width()
        if width < 1:
            width = self.get_allocation().width
        if width < 1:
            return 0
        return album_grid_content_inner_width(
            width,
            margin_start=SEARCH_VIEW_HORIZONTAL_MARGIN,
            margin_end=SEARCH_VIEW_HORIZONTAL_MARGIN,
        )

    def _search_album_tile_inner_width(self) -> int:
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
        grid = getattr(self, "_search_album_grid", None)
        if grid is not None:
            GLib.idle_add(grid._sync_layout_idle)


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


class AlbumDetailView(Gtk.Box):
    def __init__(
        self,
        *,
        service: PlayerService,
        album: Album,
        art_loader: ArtLoader | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.add_css_class("view")

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        header.set_margin_top(16)
        header.set_margin_bottom(12)
        header.set_margin_start(18)
        header.set_margin_end(18)
        self.append(header)

        art_box = Gtk.Box()
        art_box.add_css_class("card")
        art_box.set_halign(Gtk.Align.CENTER)
        art_box.append(
            _square_art(
                album,
                size=_ALBUM_DETAIL_ART_SIZE,
                art_loader=art_loader,
                css_class="album-detail-art",
            )
        )
        header.append(art_box)

        title = Gtk.Label(label=album.title, xalign=0.5, ellipsize=3)
        title.add_css_class("title-1")
        title.set_wrap(False)
        title.set_halign(Gtk.Align.CENTER)
        header.append(title)

        artist = Gtk.Label(label=album.artist_name, xalign=0.5, ellipsize=3)
        artist.add_css_class("title-4")
        artist.add_css_class("dim-label")
        artist.set_wrap(False)
        artist.set_halign(Gtk.Align.CENTER)
        header.append(artist)

        year = str(album.year) if album.year else None
        track_count = f"{album.track_count} tracks" if album.track_count else None
        info = Gtk.Label(
            label=join_detail(source_label(album.source), year, track_count),
            xalign=0.5,
            ellipsize=3,
        )
        info.add_css_class("dim-label")
        info.set_wrap(False)
        info.set_halign(Gtk.Align.CENTER)
        header.append(info)

        play_btn = Gtk.Button()
        play_btn.set_icon_name("media-playback-start-symbolic")
        play_btn.add_css_class("suggested-action")
        play_btn.add_css_class("circular")
        play_btn.set_halign(Gtk.Align.CENTER)
        play_btn.set_tooltip_text("Play album")
        play_btn.connect("clicked", lambda *_: service.play_album(album.id))
        header.append(play_btn)

        scrolled = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.append(scrolled)

        list_box = Gtk.ListBox()
        list_box.add_css_class("boxed-list")
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        list_box.connect(
            "row-activated",
            lambda _box, row: service.play_track(row.track_id),
        )
        scrolled.set_child(list_box)

        for index, track in enumerate(service.get_album_tracks(album.id)):
            list_box.append(_compact_track_row(track, index=index))


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


class AlbumTileGrid(Gtk.Box):
    """Square album tiles; column count follows this widget's allocated width."""

    def __init__(self, *, inner_width_fn: Callable[[], int] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=ALBUM_GRID_SPACING)
        self.add_css_class("album-tile-grid")
        self.set_halign(Gtk.Align.FILL)
        self.set_valign(Gtk.Align.START)
        self.set_hexpand(True)
        self.set_vexpand(False)
        self._inner_width_fn = inner_width_fn
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

    def append_album(
        self,
        album: Album,
        *,
        on_activate: Callable[[], None],
        art_loader: ArtLoader | None,
        small: bool = False,
    ) -> None:
        card = _album_card(
            album,
            art_loader=art_loader,
            small=small,
            edge=self._tile_edge,
        )
        _attach_album_card_activate(card, on_activate)
        self._cards.append(card)

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

            for start in range(0, len(self._cards), columns):
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=ALBUM_GRID_SPACING)
                row.set_halign(Gtk.Align.START)
                row.set_hexpand(False)
                row.set_vexpand(False)
                for card in self._cards[start : start + columns]:
                    _apply_album_tile_size(card, edge)
                    row.append(card)
                self.append(row)
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


def _apply_album_tile_size(card: Gtk.Widget, edge: int) -> None:
    if edge < 1:
        return
    card.set_size_request(edge, edge)


def _attach_album_card_activate(tile: Gtk.Widget, callback: Callable[[], None]) -> None:
    gesture = Gtk.GestureClick()
    gesture.connect("released", lambda *_args: callback())
    tile.add_controller(gesture)
    tile.set_focusable(True)
    tile.set_cursor_from_name("pointer")


def _square_art(
    album: Album,
    *,
    size: int,
    art_loader: ArtLoader | None,
    css_class: str = "album-card-art",
) -> Gtk.Widget:
    """Fixed square cover for album detail header."""
    frame = Gtk.Box()
    frame.add_css_class(css_class)
    frame.set_size_request(size, size)
    frame.set_halign(Gtk.Align.FILL)
    frame.set_valign(Gtk.Align.START)

    art = Gtk.Image.new_from_icon_name("audio-x-generic-symbolic")
    art.set_halign(Gtk.Align.FILL)
    art.set_valign(Gtk.Align.FILL)
    art.set_hexpand(True)
    art.set_vexpand(True)
    art.set_size_request(size, size)
    if art_loader is not None:
        art_loader.set_image(art, album.art_uri, pixel_size=size)
    else:
        art.set_pixel_size(size)
    frame.append(art)
    return frame


def _overlay_label(text: str, *, extra_classes: tuple[str, ...] = ()) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0.0, ellipsize=3)
    label.set_halign(Gtk.Align.START)
    label.set_wrap(False)
    for css_class in extra_classes:
        label.add_css_class(css_class)
    return label


def _album_card(
    album: Album,
    *,
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
    shell.set_halign(Gtk.Align.FILL)
    shell.set_valign(Gtk.Align.FILL)

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
        art_loader.set_picture(picture, album.art_uri, pixel_size=load_pixels)

    labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    labels.add_css_class("album-card-overlay")
    labels.set_valign(Gtk.Align.END)
    labels.set_halign(Gtk.Align.FILL)
    labels.set_hexpand(True)
    labels.set_vexpand(True)
    overlay.add_overlay(labels)

    labels.append(_overlay_label(album.title, extra_classes=("album-card-title",)))
    labels.append(_overlay_label(album.artist_name, extra_classes=("album-card-meta",)))
    labels.append(_overlay_label(source_label(album.source), extra_classes=("album-card-meta",)))

    return shell
