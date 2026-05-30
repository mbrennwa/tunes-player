"""Libadwaita main window and application entry."""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from tunes_player.core.services import PlayerService
from tunes_player.ui.gtk.now_playing import NowPlayingBar, attach_media_keys
from tunes_player.ui.gtk.preferences import PreferencesWindow
from tunes_player.ui.gtk.views import (
    AlbumDetailView,
    AlbumGridView,
    ArtistListView,
    QueueSheet,
    SearchResultsView,
)

_EXPANDED_SIZE = (960, 640)
_MINIMIZED_SIZE = (360, 88)


class TunesWindow(Adw.ApplicationWindow):
    def __init__(self, *, application: Adw.Application, service: PlayerService) -> None:
        super().__init__(application=application, title="Tunes")
        self._service = service
        self._minimized = False
        self._search_active = False
        self._preferences: PreferencesWindow | None = None
        self._queue_sheet: QueueSheet | None = None
        self.set_default_size(*_EXPANDED_SIZE)

        self._now_playing = NowPlayingBar(
            service=service,
            on_restore=lambda: self._set_minimized(False),
        )
        self._now_playing.set_queue_handler(self._open_queue_sheet)

        self._toolbar = Adw.ToolbarView()
        self._toolbar.add_bottom_bar(self._now_playing)
        self.set_content(self._toolbar)

        self._build_expanded_shell()
        attach_media_keys(self, service)
        service.subscribe(lambda event: GLib.idle_add(self._on_service_event, event))

    def _build_expanded_shell(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._toolbar.set_content(outer)

        self._header = Adw.HeaderBar()
        outer.append(self._header)

        self._title_label = Gtk.Label(label="Albums")
        self._title_label.add_css_class("title")
        self._header.set_title_widget(self._title_label)

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text("Search albums and tracks")
        self._search_entry.connect("search-changed", self._on_search_changed)
        self._search_entry.connect("stop-search", self._on_stop_search)

        self._search_button = Gtk.ToggleButton(icon_name="system-search-symbolic")
        self._search_button.set_tooltip_text("Search")
        self._search_button.connect("toggled", self._on_search_toggled)
        self._header.pack_start(self._search_button)

        self._search_bar = Gtk.SearchBar()
        self._search_bar.set_search_mode(False)
        self._search_bar.set_key_capture_widget(self)
        self._search_bar.connect_entry(self._search_entry)

        search_wrap = Gtk.Box()
        search_wrap.append(self._search_bar)
        outer.append(search_wrap)

        settings_btn = Gtk.Button(icon_name="emblem-system-symbolic")
        settings_btn.set_tooltip_text("Settings")
        settings_btn.connect("clicked", self._open_preferences)
        self._header.pack_end(settings_btn)

        self._minimize_btn = Gtk.Button(icon_name="window-minimize-symbolic")
        self._minimize_btn.set_tooltip_text("Minimize player")
        self._minimize_btn.connect("clicked", self._toggle_minimized)
        self._header.pack_end(self._minimize_btn)

        self._split = Adw.NavigationSplitView()
        self._split.set_vexpand(True)
        outer.append(self._split)

        condition = Adw.BreakpointCondition.parse("max-width: 720sp")
        breakpoint = Adw.Breakpoint.new(condition)
        breakpoint.add_setter(self._split, "collapsed", True)
        self.add_breakpoint(breakpoint)

        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_list = Gtk.ListBox()
        sidebar_list.add_css_class("navigation-sidebar")
        sidebar_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        sidebar_box.append(sidebar_list)

        self._sidebar_rows: dict[str, Gtk.ListBoxRow] = {}
        for section_id, label, icon in (
            ("albums", "Albums", "media-optical-symbolic"),
            ("artists", "Artists", "avatar-default-symbolic"),
            ("queue", "Queue", "view-list-symbolic"),
        ):
            row = Adw.ActionRow(title=label)
            row.set_activatable(True)
            image = Gtk.Image.new_from_icon_name(icon)
            row.add_prefix(image)
            sidebar_list.append(row)
            self._sidebar_rows[section_id] = row
            row.connect("activated", lambda _row, sid=section_id: self._on_sidebar_activated(sid))

        sidebar_page = Adw.NavigationPage(title="Library", child=sidebar_box, tag="sidebar")
        self._split.set_sidebar(sidebar_page)

        self._content_stack = Gtk.Stack()
        self._content_stack.set_vexpand(True)
        self._albums_nav = Adw.NavigationView()
        self._artists_nav = Adw.NavigationView()
        self._search_host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self._content_stack.add_named(self._albums_nav, "albums")
        self._content_stack.add_named(self._artists_nav, "artists")
        self._content_stack.add_named(self._search_host, "search")

        content_page = Adw.NavigationPage(title="", child=self._content_stack, tag="content")
        self._split.set_content(content_page)

        self._show_albums_root()
        self._show_artists_root()
        sidebar_list.select_row(self._sidebar_rows["albums"])

        self._expanded_shell = outer

    def _replace_root_page(
        self,
        nav: Adw.NavigationView,
        *,
        title: str,
        tag: str,
        child: Gtk.Widget,
    ) -> None:
        self._pop_to_root(nav)
        current = nav.get_visible_page()
        if current is not None and current.get_tag() == tag:
            current.set_child(child)
            current.set_title(title)
            return
        nav.add(Adw.NavigationPage(title=title, child=child, tag=tag))

    def _show_albums_root(self) -> None:
        albums = self._service.list_albums()
        empty_message = None
        if not albums:
            empty_message = (
                "No albums in your library.\n"
                "Open Settings, add music folders, and scan your library."
            )
        view = AlbumGridView(
            albums=albums,
            on_album_activated=self._open_album,
            empty_message=empty_message,
        )
        self._replace_root_page(
            self._albums_nav,
            title="Albums",
            tag="albums-root",
            child=view,
        )

    def _show_artists_root(self) -> None:
        artists = self._service.list_artists()
        empty_message = None
        if not artists:
            empty_message = "No artists indexed yet. Scan your library in Settings."
        view = ArtistListView(
            artists=artists,
            on_artist_activated=self._open_artist,
            empty_message=empty_message,
        )
        self._replace_root_page(
            self._artists_nav,
            title="Artists",
            tag="artists-root",
            child=view,
        )

    def _open_album(self, album_id: str) -> None:
        album = self._service.get_album(album_id)
        if album is None:
            return
        detail = AlbumDetailView(service=self._service, album=album)
        page = Adw.NavigationPage(title=album.title, child=detail, tag=album_id)

        visible = self._content_stack.get_visible_child_name()
        if visible == "search":
            self._search_button.set_active(False)
            self._content_stack.set_visible_child_name("albums")
            self._title_label.set_label("Albums")
            nav = self._albums_nav
        elif visible == "artists":
            nav = self._artists_nav
        else:
            nav = self._albums_nav

        self._pop_to_root(nav)
        nav.push(page)

    def _open_artist(self, artist_id: str) -> None:
        albums = self._service.get_artist_albums(artist_id)
        artist = next((item for item in self._service.list_artists() if item.id == artist_id), None)
        title = artist.name if artist else "Artist"
        view = AlbumGridView(
            albums=albums,
            on_album_activated=self._open_album,
        )
        page = Adw.NavigationPage(title=title, child=view, tag=artist_id)
        if self._artists_nav.get_visible_page() is not None and self._artists_nav.get_visible_page().get_tag() != "artists-root":
            self._artists_nav.pop()
        self._artists_nav.push(page)

    def _on_sidebar_activated(self, section_id: str) -> None:
        if section_id == "queue":
            self._open_queue_sheet()
            return
        if self._search_active:
            self._search_button.set_active(False)
        self._content_stack.set_visible_child_name(section_id)
        titles = {"albums": "Albums", "artists": "Artists"}
        self._title_label.set_label(titles.get(section_id, "Tunes"))

    def _on_search_toggled(self, button: Gtk.ToggleButton) -> None:
        active = button.get_active()
        self._search_active = active
        self._search_bar.set_search_mode(active)
        if active:
            self._content_stack.set_visible_child_name("search")
            self._title_label.set_label("Search")
            self._search_entry.grab_focus()
            self._refresh_search()
        elif self._content_stack.get_visible_child_name() == "search":
            self._content_stack.set_visible_child_name("albums")
            self._title_label.set_label("Albums")

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        if self._search_active:
            self._refresh_search(entry.get_text())

    def _on_stop_search(self, *_args: object) -> None:
        self._search_button.set_active(False)

    def _refresh_search(self, query: str | None = None) -> None:
        child = self._search_host.get_first_child()
        if child is not None:
            self._search_host.remove(child)
        text = query if query is not None else self._search_entry.get_text()
        if not text.strip():
            placeholder = Gtk.Label(label="Type to search your library", vexpand=True)
            placeholder.add_css_class("dim-label")
            placeholder.set_valign(Gtk.Align.CENTER)
            self._search_host.append(placeholder)
            return
        view = SearchResultsView(
            service=self._service,
            query=text,
            on_album_activated=self._open_album,
        )
        self._search_host.append(view)

    def _open_preferences(self, *_args: object) -> None:
        if self._preferences is None:
            self._preferences = PreferencesWindow(parent=self, service=self._service)
            self._preferences.connect(
                "close-request",
                lambda *_: setattr(self, "_preferences", None) or False,
            )
        self._preferences.present()

    def _on_service_event(self, event: str) -> bool:
        if event == "library_updated":
            GLib.idle_add(self._refresh_library_after_scan)
        return False

    def _refresh_library_after_scan(self) -> bool:
        self._show_albums_root()
        GLib.idle_add(self._refresh_artists_after_scan)
        return False

    def _refresh_artists_after_scan(self) -> bool:
        self._show_artists_root()
        if self._search_active:
            self._refresh_search()
        return False

    def _open_queue_sheet(self, *_args: object) -> None:
        if self._queue_sheet is None:
            self._queue_sheet = QueueSheet(service=self._service)
            self._queue_sheet.connect("closed", lambda *_: setattr(self, "_queue_sheet", None))
        self._queue_sheet.present(self)

    def _pop_to_root(self, nav: Adw.NavigationView) -> None:
        page = nav.get_visible_page()
        while page is not None and page.get_tag() not in {"albums-root", "artists-root"}:
            nav.pop()
            page = nav.get_visible_page()

    def _toggle_minimized(self, *_args: object) -> None:
        self._set_minimized(not self._minimized)

    def _set_minimized(self, minimized: bool) -> None:
        self._minimized = minimized
        if minimized:
            self._expanded_shell.set_visible(False)
            self._search_bar.get_parent().set_visible(False)
            self._now_playing.set_compact(True)
            self._now_playing.set_size_request(_MINIMIZED_SIZE[0], -1)
            self.set_size_request(_MINIMIZED_SIZE[0], _MINIMIZED_SIZE[1])
            self.set_default_size(*_MINIMIZED_SIZE)
        else:
            self._expanded_shell.set_visible(True)
            self._search_bar.get_parent().set_visible(True)
            self._now_playing.set_compact(False)
            self._now_playing.set_size_request(-1, -1)
            self.set_size_request(-1, -1)
            self.set_default_size(*_EXPANDED_SIZE)
            self.present()


def run() -> int:
    service = PlayerService()
    mpris_service = None

    class TunesApplication(Adw.Application):
        def do_activate(self) -> None:  # noqa: N802 — GTK vfunc
            window = self.get_active_window()
            if window is None:
                window = TunesWindow(application=self, service=service)
            window.present()

        def do_shutdown(self) -> None:  # noqa: N802 — GTK vfunc
            nonlocal mpris_service
            if mpris_service is not None:
                mpris_service.stop()
                mpris_service = None
            service.shutdown()
            Adw.Application.do_shutdown(self)

    app = TunesApplication(application_id="io.github.mbrennwa.Tunes")

    def _raise_app() -> None:
        window = app.get_active_window()
        if window is not None:
            window.present()

    from tunes_player.platform.linux.mpris import create_mpris_service

    mpris_service = create_mpris_service(
        service,
        on_raise=_raise_app,
        on_quit=app.quit,
    )
    mpris_service.start()

    def _poll_playback() -> bool:
        service.poll_playback()
        return True

    GLib.timeout_add(100, _poll_playback)
    return app.run(None)
