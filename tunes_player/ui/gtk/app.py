"""Libadwaita main window and application entry."""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from tunes_player.core.config import ConfigManager
from tunes_player.core.logging_config import configure_logging
from tunes_player.core.services import PlayerService
from tunes_player.platform.linux.audio import create_volume_controller
from tunes_player.ui.gtk.art import ArtLoader
from tunes_player.ui.gtk.errors import attach_error_toasts, show_error_toast
from tunes_player.ui.gtk.now_playing import NowPlayingBar, attach_media_keys
from tunes_player.ui.gtk.preferences import PreferencesWindow
from tunes_player.ui.gtk.util import escape_markup, load_app_css
from tunes_player.ui.gtk.views import (
    ArtistListView,
    PlaceholderView,
    QueueSheet,
    RecentlyAddedGridView,
    ReleaseDetailView,
    ReleaseGridView,
    SearchResultsView,
)
from tunes_player.ui.gtk.album_grid import (
    ALBUM_GRID_VIEW_MARGIN,
    SEARCH_VIEW_HORIZONTAL_MARGIN,
    album_grid_min_content_width,
)

_DEFAULT_SIZE = (960, 640)
_SIDEBAR_WIDTH_PADDING_SP = 16.0
_DISCOVER_SECTION_TITLES = {
    "new-music": "New Music",
    "suggestions": "Suggestions",
}
_SUGGESTIONS_PLACEHOLDER = (
    "Suggestions from your library and streaming services will appear here."
)
_NAV_ROOT_TAGS = frozenset({"releases-root", "artists-root", "search-root", "discover-root"})
_NAV_ROOT_TITLES = {
    "releases-root": "Releases",
    "artists-root": "Artists",
    "search-root": "Search",
    "discover-root": "Discover",
}


class TunesWindow(Adw.ApplicationWindow):
    def __init__(self, *, application: Adw.Application, service: PlayerService) -> None:
        super().__init__(application=application, title="Tunes")
        self._service = service
        self._art_loader = ArtLoader(service.config.data_dir)
        self._search_active = False
        self._discover_current_title = ""
        self._discover_active_section_id: str | None = None
        self._preferences: PreferencesWindow | None = None
        self._queue_sheet: QueueSheet | None = None
        self.set_default_size(*_DEFAULT_SIZE)

        self._now_playing = NowPlayingBar(service=service, art_loader=self._art_loader)
        self._now_playing.set_queue_handler(self._open_queue_sheet)
        self._now_playing.set_play_handler(self._on_play_clicked)

        self._toolbar = Adw.ToolbarView()
        self._toolbar.set_size_request(-1, -1)
        self._toolbar.add_bottom_bar(self._now_playing)
        self.set_content(self._toolbar)

        self._toast_overlay = Adw.ToastOverlay()
        self._toast_overlay.set_size_request(-1, -1)
        self._build_expanded_shell()
        attach_media_keys(self, service)
        attach_error_toasts(self._toast_overlay, service)
        service.subscribe(lambda event: GLib.idle_add(self._on_service_event, event))
        GLib.idle_add(self._startup_playback_probe)

    def _build_expanded_shell(self) -> None:
        self._header = Adw.HeaderBar()
        self._toolbar.add_top_bar(self._header)

        self._window_title = Adw.WindowTitle(title="Releases", subtitle="")
        self._title_center = Gtk.Box()
        self._title_center.set_halign(Gtk.Align.CENTER)
        self._title_center.append(self._window_title)
        self._header.set_title_widget(self._title_center)

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text("Search library and TIDAL")
        self._search_entry.set_width_chars(24)
        self._search_entry.set_max_width_chars(24)
        self._search_entry.set_hexpand(False)
        self._search_entry.connect("search-changed", self._on_search_changed)
        self._search_entry.connect("stop-search", self._on_stop_search)

        self._back_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self._back_btn.set_tooltip_text("Back")
        self._back_btn.set_visible(False)
        self._back_btn.connect("clicked", self._on_nav_back)
        self._header.pack_start(self._back_btn)

        self._search_button = Gtk.ToggleButton(icon_name="system-search-symbolic")
        self._search_button.set_tooltip_text("Search")
        self._search_button.connect("toggled", self._on_search_toggled)
        self._header.pack_start(self._search_button)

        self._split = Adw.NavigationSplitView()
        self._split.set_size_request(-1, -1)
        self._split.set_hexpand(True)
        self._split.set_vexpand(True)
        # Default 25% fraction resizes the sidebar with the window; min=max locks width instead.
        self._split.set_sidebar_width_fraction(0.0)
        self._toast_overlay.set_child(self._split)
        self._toolbar.set_content(self._toast_overlay)

        self._sidebar_shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._sidebar_shell.set_vexpand(True)
        self._sidebar_shell.set_hexpand(False)
        self._sidebar_shell.set_halign(Gtk.Align.START)

        self._sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._sidebar_box.set_vexpand(True)
        self._sidebar_box.set_hexpand(False)
        self._sidebar_shell.append(self._sidebar_box)

        self._sidebar_rows: dict[str, Gtk.ListBoxRow] = {}

        self._sidebar_nav_list = Gtk.ListBox()
        self._sidebar_nav_list.add_css_class("navigation-sidebar")
        self._sidebar_nav_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._sidebar_nav_list.set_vexpand(True)
        self._sidebar_box.append(self._sidebar_nav_list)

        for section_id, label, icon in (
            ("new-music", "New Music", "list-add-symbolic"),
            ("suggestions", "Suggestions", "applications-science-symbolic"),
            ("releases", "Releases", "media-optical-symbolic"),
            ("artists", "Artists", "avatar-default-symbolic"),
        ):
            row = Adw.ActionRow(title=label)
            row.set_activatable(True)
            image = Gtk.Image.new_from_icon_name(icon)
            row.add_prefix(image)
            self._sidebar_nav_list.append(row)
            self._sidebar_rows[section_id] = row
            row.connect("activated", lambda _row, sid=section_id: self._on_sidebar_activated(sid))

        self._sidebar_settings_list = Gtk.ListBox()
        self._sidebar_settings_list.add_css_class("navigation-sidebar")
        self._sidebar_settings_list.set_selection_mode(Gtk.SelectionMode.NONE)
        settings_row = Adw.ActionRow(title="Settings...")
        settings_row.set_activatable(True)
        settings_row.add_prefix(Gtk.Image.new_from_icon_name("emblem-system-symbolic"))
        self._sidebar_settings_list.append(settings_row)
        settings_row.connect("activated", self._open_preferences)
        self._sidebar_box.append(self._sidebar_settings_list)

        self._apply_fixed_sidebar_width()
        sidebar_page = Adw.NavigationPage(title="Library", child=self._sidebar_shell, tag="sidebar")
        self._split.set_sidebar(sidebar_page)
        self._sidebar_shell.connect("map", lambda *_args: GLib.idle_add(self._apply_fixed_sidebar_width))

        self._content_stack = Gtk.Stack()
        self._content_stack.set_size_request(-1, -1)
        self._content_stack.set_vexpand(True)
        self._releases_nav = Adw.NavigationView()
        self._artists_nav = Adw.NavigationView()
        self._discover_nav = Adw.NavigationView()
        for nav in (self._releases_nav, self._artists_nav, self._discover_nav):
            nav.connect("notify::visible-page", self._on_nav_visible_page_changed)
        self._search_nav = Adw.NavigationView()
        self._search_nav.connect("notify::visible-page", self._on_nav_visible_page_changed)
        self._content_stack.add_named(self._releases_nav, "releases")
        self._content_stack.add_named(self._artists_nav, "artists")
        self._content_stack.add_named(self._discover_nav, "discover")
        self._content_stack.add_named(self._search_nav, "search")

        content_page = Adw.NavigationPage(title="", child=self._content_stack, tag="content")
        self._split.set_content(content_page)

        self._sidebar_width_sp = 0.0
        self._show_releases_root()
        self._show_artists_root()
        self._sidebar_nav_list.select_row(self._sidebar_rows["releases"])

    def _release_id_for_current_view(self) -> str | None:
        nav = self._active_nav_view()
        if nav is None:
            return None
        page = nav.get_visible_page()
        if page is None:
            return None
        tag = page.get_tag()
        if not tag or tag in _NAV_ROOT_TAGS:
            return None
        release = self._service.get_release(tag)
        return tag if release is not None else None

    def _on_play_clicked(self) -> None:
        state = self._service.get_playback_state()
        if state.current_track is None and not state.queue:
            release_id = self._release_id_for_current_view()
            if release_id is not None:
                self._service.play_release(release_id, start_index=0)
                return
        self._service.toggle_play_pause()

    def _compute_sidebar_width_sp(self) -> float:
        max_nat = 0
        for list_box in (self._sidebar_nav_list, self._sidebar_settings_list):
            row = list_box.get_first_child()
            while row is not None:
                _min_w, nat_w, _min_baseline, _nat_baseline = row.measure(
                    Gtk.Orientation.HORIZONTAL,
                    -1,
                )
                max_nat = max(max_nat, nat_w)
                row = row.get_next_sibling()
        return float(max_nat) + _SIDEBAR_WIDTH_PADDING_SP

    def _apply_fixed_sidebar_width(self) -> bool:
        width_sp = self._compute_sidebar_width_sp()
        self._sidebar_width_sp = width_sp
        self._split.set_min_sidebar_width(width_sp)
        self._split.set_max_sidebar_width(width_sp)
        self._apply_window_min_width()
        return False

    def _apply_window_min_width(self) -> None:
        sidebar = self._sidebar_width_sp or self._split.get_max_sidebar_width()
        min_width = int(sidebar) + album_grid_min_content_width()
        _min_w, min_h = self.get_size_request()
        if min_h < 0:
            min_h = 400
        self.set_size_request(min_width, min_h)

    def _sidebar_width_pixels(self) -> int:
        sidebar_page = self._split.get_sidebar()
        if sidebar_page is not None:
            width = sidebar_page.get_width()
            if width >= 64:
                return width
            alloc = sidebar_page.get_allocation()
            if alloc.width > 0:
                return alloc.width
        cached = self._sidebar_width_sp
        if cached > 0:
            return int(cached)
        max_width = self._split.get_max_sidebar_width()
        return int(max_width) if max_width > 0 else 0

    def _album_grid_inner_width(self) -> int:
        window_width = self.get_width()
        if window_width < 64:
            return 0
        return max(
            0,
            window_width - self._sidebar_width_pixels() - 2 * ALBUM_GRID_VIEW_MARGIN,
        )

    def _search_grid_inner_width(self) -> int:
        window_width = self.get_width()
        if window_width < 64:
            return 0
        return max(
            0,
            window_width - self._sidebar_width_pixels() - 2 * SEARCH_VIEW_HORIZONTAL_MARGIN,
        )

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
            current.set_title(escape_markup(title))
            return
        nav.add(Adw.NavigationPage(title=escape_markup(title), child=child, tag=tag))

    def _show_releases_root(self) -> None:
        releases = self._service.list_releases()
        empty_message = None
        if not releases:
            empty_message = (
                "No releases in your library.\n"
                "Open Settings, add music folders, and scan your library."
            )
        view = ReleaseGridView(
            releases=releases,
            on_release_activated=self._open_release,
            empty_message=empty_message,
            art_loader=self._art_loader,
            window_inner_width_fn=self._album_grid_inner_width,
        )
        self._replace_root_page(
            self._releases_nav,
            title="Releases",
            tag="releases-root",
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

    def _open_release(self, release_id: str) -> None:
        release = self._service.get_release(release_id)
        if release is None:
            return
        detail = ReleaseDetailView(
            service=self._service,
            release=release,
            art_loader=self._art_loader,
        )
        page = Adw.NavigationPage(
            title=escape_markup(release.title),
            child=detail,
            tag=release_id,
        )

        visible = self._content_stack.get_visible_child_name()
        if visible == "search":
            nav = self._search_nav
            if not self._nav_at_root(nav):
                nav.pop()
        elif visible == "artists":
            nav = self._artists_nav
            self._pop_to_root(nav)
        elif visible == "discover":
            nav = self._discover_nav
            self._pop_to_root(nav)
        else:
            nav = self._releases_nav
            self._pop_to_root(nav)

        nav.push(page)
        self._sync_header_with_nav()

    def _open_artist(self, artist_id: str) -> None:
        releases = self._service.get_artist_releases(artist_id)
        artist = next((item for item in self._service.list_artists() if item.id == artist_id), None)
        title = artist.name if artist else "Artist"
        view = ReleaseGridView(
            releases=releases,
            on_release_activated=self._open_release,
            art_loader=self._art_loader,
            window_inner_width_fn=self._album_grid_inner_width,
        )
        page = Adw.NavigationPage(title=escape_markup(title), child=view, tag=artist_id)
        if self._artists_nav.get_visible_page() is not None and self._artists_nav.get_visible_page().get_tag() != "artists-root":
            self._artists_nav.pop()
        self._artists_nav.push(page)
        self._sync_header_with_nav()

    def _on_sidebar_activated(self, section_id: str) -> None:
        if section_id in _DISCOVER_SECTION_TITLES:
            if self._search_active:
                self._search_button.set_active(False)
            self._show_discover_root(section_id)
            self._content_stack.set_visible_child_name("discover")
            self._sync_header_with_nav()
            return
        if self._search_active:
            self._search_button.set_active(False)
        if section_id == "releases":
            self._pop_to_root(self._releases_nav)
        elif section_id == "artists":
            self._pop_to_root(self._artists_nav)
        self._content_stack.set_visible_child_name(section_id)
        self._sync_header_with_nav()

    def _show_discover_root(self, section_id: str) -> None:
        title = _DISCOVER_SECTION_TITLES.get(section_id, "Discover")
        self._discover_current_title = title
        self._discover_active_section_id = section_id
        if section_id == "new-music":
            items = self._service.list_recently_added_items()
            empty_message = (
                "Nothing new in the last 30 days.\n"
                "Scan your library in Settings → Sources, or sign in to TIDAL for new releases."
                if not items
                else None
            )
            view = RecentlyAddedGridView(
                items=items,
                on_release_activated=self._open_release,
                empty_message=empty_message,
                art_loader=self._art_loader,
                window_inner_width_fn=self._album_grid_inner_width,
            )
        elif section_id == "suggestions":
            view = PlaceholderView(
                title=title,
                message=_SUGGESTIONS_PLACEHOLDER,
            )
        else:
            view = PlaceholderView(
                title=title,
                message="Not implemented yet.",
            )
        self._replace_root_page(
            self._discover_nav,
            title=title,
            tag="discover-root",
            child=view,
        )

    def _on_search_toggled(self, button: Gtk.ToggleButton) -> None:
        active = button.get_active()
        self._search_active = active
        if active:
            self._title_center.remove(self._window_title)
            self._title_center.append(self._search_entry)
            self._content_stack.set_visible_child_name("search")
            self._search_entry.grab_focus()
            self._refresh_search()
        else:
            self._title_center.remove(self._search_entry)
            self._title_center.append(self._window_title)
            self._pop_to_root(self._search_nav)
            self._search_entry.set_text("")
            if self._content_stack.get_visible_child_name() == "search":
                self._content_stack.set_visible_child_name("releases")
            self._sync_header_with_nav()

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        if self._search_active:
            self._refresh_search(entry.get_text())

    def _on_stop_search(self, *_args: object) -> None:
        self._search_button.set_active(False)

    def _refresh_search(self, query: str | None = None) -> None:
        if not self._nav_at_root(self._search_nav):
            self._pop_to_root(self._search_nav)
        text = query if query is not None else self._search_entry.get_text()
        if not text.strip():
            placeholder = Gtk.Label(label="Type to search your library and TIDAL", vexpand=True)
            placeholder.add_css_class("dim-label")
            placeholder.set_valign(Gtk.Align.CENTER)
            self._replace_root_page(
                self._search_nav,
                title="Search",
                tag="search-root",
                child=placeholder,
            )
            return
        view = SearchResultsView(
            service=self._service,
            query=text,
            on_release_activated=self._open_release,
            art_loader=self._art_loader,
            window_inner_width_fn=self._search_grid_inner_width,
        )
        self._replace_root_page(
            self._search_nav,
            title="Search",
            tag="search-root",
            child=view,
        )

    def _open_preferences(self, *_args: object) -> None:
        if self._preferences is None:
            self._preferences = PreferencesWindow(parent=self, service=self._service)
            self._preferences.connect(
                "close-request",
                lambda *_: setattr(self, "_preferences", None) or False,
            )
        self._preferences.present()

    def _startup_playback_probe(self) -> bool:
        message = self._service.playback_available()
        if message:
            show_error_toast(self._toast_overlay, message)
        return False

    def _on_service_event(self, event: str) -> bool:
        if event == "library_updated":
            GLib.idle_add(self._refresh_library_after_scan)
        elif event == "sources_changed":
            GLib.idle_add(self._refresh_discover_if_needed)
        return False

    def _refresh_library_after_scan(self) -> bool:
        self._show_releases_root()
        GLib.idle_add(self._refresh_artists_after_scan)
        return False

    def _refresh_artists_after_scan(self) -> bool:
        self._show_artists_root()
        if self._search_active:
            self._refresh_search()
        GLib.idle_add(self._refresh_discover_if_needed)
        return False

    def _refresh_discover_if_needed(self) -> bool:
        if (
            self._content_stack.get_visible_child_name() == "discover"
            and self._discover_active_section_id == "new-music"
        ):
            self._show_discover_root("new-music")
        return False

    def _open_queue_sheet(self, *_args: object) -> None:
        if self._queue_sheet is None:
            self._queue_sheet = QueueSheet(service=self._service)
            self._queue_sheet.connect("closed", lambda *_: setattr(self, "_queue_sheet", None))
        self._queue_sheet.present(self)

    def _pop_to_root(self, nav: Adw.NavigationView) -> None:
        page = nav.get_visible_page()
        while page is not None and page.get_tag() not in _NAV_ROOT_TAGS:
            nav.pop()
            page = nav.get_visible_page()

    def _active_nav_view(self) -> Adw.NavigationView | None:
        if self._search_active:
            return self._search_nav
        section = self._content_stack.get_visible_child_name()
        if section == "releases":
            return self._releases_nav
        if section == "artists":
            return self._artists_nav
        if section == "discover":
            return self._discover_nav
        return None

    def _nav_at_root(self, nav: Adw.NavigationView) -> bool:
        page = nav.get_visible_page()
        if page is None:
            return True
        return page.get_tag() in _NAV_ROOT_TAGS

    def _title_for_nav_page(self, page: Adw.NavigationPage) -> str | None:
        tag = page.get_tag()
        if not tag or tag in _NAV_ROOT_TAGS:
            return None
        release = self._service.get_release(tag)
        if release is not None:
            return release.title
        for artist in self._service.list_artists():
            if artist.id == tag:
                return artist.name
        return None

    def _sync_header_with_nav(self) -> None:
        nav = self._active_nav_view()
        if nav is None:
            self._back_btn.set_visible(False)
            return
        at_root = self._nav_at_root(nav)
        self._back_btn.set_visible(not at_root)
        page = nav.get_visible_page()
        if page is None:
            self._window_title.set_title("Tunes")
            return
        if at_root:
            if self._content_stack.get_visible_child_name() == "discover":
                self._window_title.set_title(self._discover_current_title or "Discover")
            else:
                self._window_title.set_title(_NAV_ROOT_TITLES.get(page.get_tag(), "Tunes"))
            return
        title = self._title_for_nav_page(page)
        if title:
            self._window_title.set_title(title)

    def _on_nav_back(self, *_args: object) -> None:
        nav = self._active_nav_view()
        if nav is None or self._nav_at_root(nav):
            return
        nav.pop()

    def _on_nav_visible_page_changed(self, *_args: object) -> None:
        self._sync_header_with_nav()


def run() -> int:
    load_app_css()
    config = ConfigManager()
    config.load()
    configure_logging(config.data_dir)
    volume_controller = create_volume_controller(config.config)
    service = PlayerService(config=config, volume_controller=volume_controller)
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

    app = TunesApplication(application_id="tunes.player")

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
