"""Browse and detail views."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from tunes_player.core.models import Album, Artist, Track
from tunes_player.core.services import PlayerService
from tunes_player.ui.gtk.art import ArtLoader
from tunes_player.ui.gtk.util import (
    escape_markup,
    format_duration,
    format_track_number,
    join_detail,
    source_label,
)


class AlbumGridView(Gtk.ScrolledWindow):
    def __init__(
        self,
        *,
        albums: list[Album],
        on_album_activated: Callable[[str], None],
        empty_message: str | None = None,
        art_loader: ArtLoader | None = None,
    ) -> None:
        super().__init__(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.set_propagate_natural_width(False)
        self.add_css_class("view")

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

        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        shell.set_hexpand(True)
        shell.set_halign(Gtk.Align.FILL)

        grid = AlbumTileGrid()
        grid.set_margin_top(18)
        grid.set_margin_bottom(18)
        grid.set_margin_start(18)
        grid.set_margin_end(18)
        shell.append(grid)
        self.set_child(shell)
        grid.bind_viewport(self)

        self._populate_albums(grid, albums, on_album_activated, art_loader=art_loader)
        GLib.idle_add(grid.relayout)

    @staticmethod
    def _populate_albums(
        grid: AlbumTileGrid,
        albums: list[Album],
        on_album_activated: Callable[[str], None],
        *,
        art_loader: ArtLoader | None = None,
        start: int = 0,
        batch_size: int = 24,
    ) -> None:
        end = min(start + batch_size, len(albums))
        for album in albums[start:end]:
            grid.append_album(
                album,
                on_activate=lambda album_id=album.id: on_album_activated(album_id),
                art_loader=art_loader,
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
            )
        else:
            GLib.idle_add(grid.relayout)


class ArtistListView(Gtk.ScrolledWindow):
    def __init__(
        self,
        *,
        artists: list[Artist],
        on_artist_activated: Callable[[str], None],
        empty_message: str | None = None,
    ) -> None:
        super().__init__(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
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

        ArtistListView._populate_artists(list_box, artists, on_artist_activated)

    @staticmethod
    def _populate_artists(
        list_box: Gtk.ListBox,
        artists: list[Artist],
        on_artist_activated: Callable[[str], None],
        *,
        start: int = 0,
        batch_size: int = 50,
    ) -> None:
        end = min(start + batch_size, len(artists))
        for artist in artists[start:end]:
            row = Adw.ActionRow(title=escape_markup(artist.name), subtitle="Artist")
            row.set_activatable(True)
            row.connect(
                "activated",
                lambda _row, artist_id=artist.id: on_artist_activated(artist_id),
            )
            list_box.append(row)

        if end < len(artists):
            GLib.idle_add(
                ArtistListView._populate_artists,
                list_box,
                artists,
                on_artist_activated,
                start=end,
                batch_size=batch_size,
            )


_ALBUM_TILE_ART_PIXELS = 512
_ALBUM_TILE_ART_PIXELS_SMALL = 384
_ALBUM_DETAIL_ART_SIZE = 220
_ALBUM_GRID_SPACING = 12
_ALBUM_TILE_MIN_EDGE = 140
_ALBUM_TILE_MAX_EDGE = 200


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


class SearchResultsView(Gtk.ScrolledWindow):
    def __init__(
        self,
        *,
        service: PlayerService,
        query: str,
        on_album_activated: Callable[[str], None],
        art_loader: ArtLoader | None = None,
    ) -> None:
        super().__init__(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.set_propagate_natural_width(False)
        self.add_css_class("view")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, vexpand=True)
        box.set_hexpand(True)
        box.set_halign(Gtk.Align.FILL)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        self.set_child(box)

        results = service.search(query)
        if not results.albums and not results.tracks:
            empty = Gtk.Label(label=f'No results for “{query}”', vexpand=True)
            empty.add_css_class("dim-label")
            box.append(empty)
            return

        if results.albums:
            box.append(_section_label("Albums"))
            album_grid = AlbumTileGrid()
            box.append(album_grid)
            album_grid.bind_viewport(self)
            for album in results.albums:
                album_grid.append_album(
                    album,
                    on_activate=lambda album_id=album.id: on_album_activated(album_id),
                    art_loader=art_loader,
                    small=True,
                )
            GLib.idle_add(album_grid.relayout)

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


def _section_label(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.add_css_class("heading")
    return label


def _widget_allocated_width(widget: Gtk.Widget) -> int:
    width = widget.get_width()
    if width >= 64:
        return width
    allocation = widget.get_allocation()
    return allocation.width if allocation.width >= 64 else 0


def _find_ancestor(widget: Gtk.Widget, type_: type[Gtk.Widget]) -> Gtk.Widget | None:
    node: Gtk.Widget | None = widget
    while node is not None:
        if isinstance(node, type_):
            return node
        node = node.get_parent()
    return None


def _horizontal_margins_until(widget: Gtk.Widget, stop: Gtk.Widget | None) -> int:
    extra = 0
    node: Gtk.Widget | None = widget
    while node is not None and node is not stop:
        extra += node.get_margin_start() + node.get_margin_end()
        node = node.get_parent()
    return extra


def _viewport_inner_width(widget: Gtk.Widget) -> int:
    viewport = getattr(widget, "_viewport", None)
    if viewport is None:
        return 0
    width = _widget_allocated_width(viewport)
    if width < 64:
        return 0
    return max(0, width - _horizontal_margins_until(widget, viewport))


def _window_inner_width(widget: Gtk.Widget) -> int:
    """Content width from the window; shrinks before the scroll child min-width does."""
    root = widget.get_root()
    if not isinstance(root, Gtk.Window):
        return 0

    width = _widget_allocated_width(root)
    if width < 64:
        return 0

    split = _find_ancestor(widget, Adw.NavigationSplitView)
    if split is not None and not split.get_collapsed():
        sidebar = split.get_sidebar()
        if sidebar is not None:
            sidebar_w = _widget_allocated_width(sidebar)
            if 0 < sidebar_w < width:
                width -= sidebar_w

    viewport = getattr(widget, "_viewport", None)
    if viewport is not None:
        return max(0, width - _horizontal_margins_until(widget, viewport))
    return width


def _content_area_inner_width(widget: Gtk.Widget) -> int:
    """Use the smaller of viewport and window width (viewport lags on shrink)."""
    candidates = [_viewport_inner_width(widget), _window_inner_width(widget)]
    usable = [value for value in candidates if value >= 64]
    if not usable:
        return 0
    return min(usable)


def _album_grid_layout(inner_width: int) -> tuple[int, int]:
    """Return (columns, tile_edge) that fill inner_width with fixed gaps."""
    spacing = _ALBUM_GRID_SPACING
    min_edge = _ALBUM_TILE_MIN_EDGE
    max_edge = _ALBUM_TILE_MAX_EDGE

    if inner_width < min_edge:
        return 1, min_edge

    slot_max = max_edge + spacing
    # Fewest columns so tile edge does not exceed max_edge.
    columns = max(1, (inner_width + spacing + max_edge - 1) // slot_max)
    edge = (inner_width - spacing * (columns - 1)) // columns

    if edge < min_edge:
        columns = max(1, (inner_width + spacing) // (min_edge + spacing))
        edge = (inner_width - spacing * (columns - 1)) // columns

    edge = max(min_edge, min(edge, max_edge))
    return columns, edge


def _reset_album_tile_size(root: Gtk.Widget) -> None:
    root.set_size_request(-1, -1)
    if isinstance(root, Gtk.AspectFrame):
        tile = root.get_child()
        if tile is not None:
            tile.set_size_request(-1, -1)


def _apply_album_tile_size(root: Gtk.Widget, edge: int) -> None:
    if edge < 1:
        return
    root.set_size_request(edge, edge)
    if isinstance(root, Gtk.AspectFrame):
        tile = root.get_child()
        if tile is not None:
            tile.set_size_request(edge, edge)
    root.queue_resize()


class AlbumTileGrid(Gtk.Box):
    """Square album tiles in rows; column count follows available pane width."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=_ALBUM_GRID_SPACING)
        self.add_css_class("album-grid")
        self.set_hexpand(False)
        self.set_vexpand(False)
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)
        self._cards: list[Gtk.Widget] = []
        self._columns = 1
        self._tile_edge = _ALBUM_TILE_MAX_EDGE
        self._layout_key: tuple[int, int, int, int] | None = None
        self._relayout_idle_id = 0
        self._tick_callback_id = 0
        self._viewport: Gtk.ScrolledWindow | None = None
        self.connect("map", self._start_resize_watch)
        self.connect("unmap", self._stop_resize_watch)

    def bind_viewport(self, viewport: Gtk.ScrolledWindow) -> None:
        self._viewport = viewport

        def on_resize(*_args: object) -> None:
            self._schedule_relayout()

        viewport.connect_after("notify::width", on_resize)
        ancestor: Gtk.Widget | None = viewport.get_parent()
        while ancestor is not None:
            ancestor.connect_after("notify::width", on_resize)
            if isinstance(ancestor, Adw.NavigationSplitView):
                ancestor.connect_after("notify::collapsed", on_resize)
            ancestor = ancestor.get_parent()

        root = viewport.get_root()
        if isinstance(root, Gtk.Window):
            root.connect_after("notify::width", on_resize)
            root.connect_after("notify::default-width", on_resize)

    def _start_resize_watch(self, *_args: object) -> None:
        self._schedule_relayout()
        if self._tick_callback_id:
            return
        self._tick_callback_id = self.add_tick_callback(self._on_frame_tick)

    def _stop_resize_watch(self, *_args: object) -> None:
        if self._tick_callback_id:
            self.remove_tick_callback(self._tick_callback_id)
            self._tick_callback_id = 0

    def _on_frame_tick(self, _widget: Gtk.Widget, _frame_clock: object) -> bool:
        inner = _content_area_inner_width(self)
        if inner < 1:
            return True
        columns, edge = _album_grid_layout(inner)
        key = (inner, columns, edge, len(self._cards))
        if key != self._layout_key:
            self.relayout()
        return True

    def _schedule_relayout(self) -> None:
        if self._relayout_idle_id:
            return
        self._relayout_idle_id = GLib.idle_add(self._relayout_idle, priority=GLib.PRIORITY_LOW)

    def _relayout_idle(self) -> bool:
        self._relayout_idle_id = 0
        self.relayout()
        return False

    def _clear_rows(self) -> None:
        child = self.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.remove(child)
            child = next_child

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
            edge=self._tile_edge,
            small=small,
            art_loader=art_loader,
        )
        _attach_album_card_activate(card, on_activate)
        self._cards.append(card)
        self._schedule_relayout()

    def _release_wide_minimums(self) -> None:
        self.set_size_request(-1, -1)
        self._clear_rows()
        for card in self._cards:
            _reset_album_tile_size(card)

    def relayout(self) -> bool:
        inner = _content_area_inner_width(self)
        if inner < 1:
            return False

        columns, edge = _album_grid_layout(inner)
        layout_key = (inner, columns, edge, len(self._cards))
        if layout_key == self._layout_key:
            return False

        self._layout_key = layout_key
        self._columns = columns
        self._tile_edge = edge
        self._release_wide_minimums()

        for start in range(0, len(self._cards), columns):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=_ALBUM_GRID_SPACING)
            row.set_halign(Gtk.Align.START)
            row.set_hexpand(False)
            row.set_vexpand(False)
            for card in self._cards[start : start + columns]:
                _apply_album_tile_size(card, edge)
                row.append(card)
            self.append(row)

        self.queue_resize()
        if self._viewport is not None:
            self._viewport.queue_resize()
        return False


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
    edge: int,
    small: bool = False,
    art_loader: ArtLoader | None = None,
) -> Gtk.AspectFrame:
    """Square tile: cover fills the cell; title/artist/source overlaid at the bottom."""
    frame = Gtk.AspectFrame()
    frame.set_ratio(1.0)
    frame.set_obey_child(True)
    frame.add_css_class("album-card-frame")
    frame.set_hexpand(False)
    frame.set_vexpand(False)
    frame.set_halign(Gtk.Align.START)
    frame.set_valign(Gtk.Align.START)
    _apply_album_tile_size(frame, edge)

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

    return frame
