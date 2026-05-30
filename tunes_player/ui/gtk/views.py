"""Browse and detail views."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from tunes_player.core.models import Album, Artist
from tunes_player.core.services import PlayerService
from tunes_player.ui.gtk.art import ArtLoader
from tunes_player.ui.gtk.util import format_duration


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

        flow = Gtk.FlowBox()
        flow.set_valign(Gtk.Align.START)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(4)
        flow.set_min_children_per_line(2)
        flow.set_column_spacing(18)
        flow.set_row_spacing(18)
        flow.set_margin_top(18)
        flow.set_margin_bottom(18)
        flow.set_margin_start(18)
        flow.set_margin_end(18)
        self.set_child(flow)

        self._populate_albums(flow, albums, on_album_activated, art_loader=art_loader)

    @staticmethod
    def _populate_albums(
        flow: Gtk.FlowBox,
        albums: list[Album],
        on_album_activated: Callable[[str], None],
        *,
        art_loader: ArtLoader | None = None,
        start: int = 0,
        batch_size: int = 24,
    ) -> None:
        end = min(start + batch_size, len(albums))
        for album in albums[start:end]:
            card = _album_card(album, art_loader=art_loader)
            card.connect("clicked", lambda _btn, album_id=album.id: on_album_activated(album_id))
            flow.append(card)

        if end < len(albums):
            GLib.idle_add(
                AlbumGridView._populate_albums,
                flow,
                albums,
                on_album_activated,
                art_loader,
                start=end,
                batch_size=batch_size,
            )


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
            row = Adw.ActionRow(title=artist.name, subtitle="Artist")
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

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        header.set_margin_top(18)
        header.set_margin_bottom(12)
        header.set_margin_start(18)
        header.set_margin_end(18)
        self.append(header)

        art = Gtk.Image.new_from_icon_name("audio-x-generic-symbolic")
        art.set_pixel_size(160)
        art.add_css_class("card")
        if art_loader is not None:
            art_loader.set_image(art, album.art_uri, pixel_size=160)
        header.append(art)

        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, vexpand=True)
        meta.set_valign(Gtk.Align.CENTER)
        header.append(meta)

        title = Gtk.Label(label=album.title, xalign=0)
        title.add_css_class("title-1")
        meta.append(title)

        artist = Gtk.Label(label=album.artist_name, xalign=0)
        artist.add_css_class("title-4")
        artist.add_css_class("dim-label")
        meta.append(artist)

        year = str(album.year) if album.year else ""
        subtitle = f"{year} · {album.track_count} tracks".strip(" · ")
        info = Gtk.Label(label=subtitle, xalign=0)
        info.add_css_class("dim-label")
        meta.append(info)

        play_btn = Gtk.Button(label="Play Album")
        play_btn.add_css_class("suggested-action")
        play_btn.connect("clicked", lambda *_: service.play_album(album.id))
        meta.append(play_btn)

        scrolled = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.append(scrolled)

        list_box = Gtk.ListBox()
        list_box.add_css_class("boxed-list")
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled.set_child(list_box)

        for index, track in enumerate(service.get_album_tracks(album.id)):
            row = Adw.ActionRow(
                title=track.title,
                subtitle=format_duration(track.duration_sec),
            )
            row.set_activatable(True)
            row.connect(
                "activated",
                lambda _row, track_id=track.id: service.play_track(track_id),
            )
            list_box.append(row)


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
        self.add_css_class("view")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, vexpand=True)
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
            album_flow = Gtk.FlowBox()
            album_flow.set_selection_mode(Gtk.SelectionMode.NONE)
            album_flow.set_max_children_per_line(3)
            album_flow.set_column_spacing(12)
            album_flow.set_row_spacing(12)
            box.append(album_flow)
            for album in results.albums:
                card = _album_card(album, small=True, art_loader=art_loader)
                card.connect(
                    "clicked",
                    lambda _btn, album_id=album.id: on_album_activated(album_id),
                )
                album_flow.append(card)

        if results.tracks:
            box.append(_section_label("Tracks"))
            track_list = Gtk.ListBox()
            track_list.add_css_class("boxed-list")
            track_list.set_selection_mode(Gtk.SelectionMode.NONE)
            box.append(track_list)
            for track in results.tracks:
                row = Adw.ActionRow(
                    title=track.title,
                    subtitle=f"{track.artist_name} · {track.album_title or ''}".strip(" · "),
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
                title=track.title,
                subtitle=f"{track.artist_name} · {track.album_title or ''}".strip(" · "),
            )
            if index == state.queue_index:
                row.add_prefix(Gtk.Image.new_from_icon_name("media-playback-start-symbolic"))
            row.set_activatable(True)
            row.connect(
                "activated",
                lambda _row, track_id=track.id: self._service.play_track(track_id),
            )
            self._list_box.append(row)


def _section_label(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.add_css_class("heading")
    return label


def _album_card(
    album: Album,
    *,
    small: bool = False,
    art_loader: ArtLoader | None = None,
) -> Gtk.Button:
    button = Gtk.Button()
    button.add_css_class("card")
    button.add_css_class("album-card")

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_margin_top(12)
    box.set_margin_bottom(12)
    box.set_margin_start(12)
    box.set_margin_end(12)
    button.set_child(box)

    art = Gtk.Image.new_from_icon_name("audio-x-generic-symbolic")
    pixel_size = 96 if not small else 72
    art.set_pixel_size(pixel_size)
    if art_loader is not None:
        art_loader.set_image(art, album.art_uri, pixel_size=pixel_size)
    box.append(art)

    title = Gtk.Label(label=album.title, wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=18)
    title.add_css_class("heading")
    box.append(title)

    artist = Gtk.Label(label=album.artist_name, wrap=True, justify=Gtk.Justification.CENTER)
    artist.add_css_class("dim-label")
    artist.add_css_class("caption")
    box.append(artist)

    return button
