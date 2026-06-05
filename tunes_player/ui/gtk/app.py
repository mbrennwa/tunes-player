"""Libadwaita main window and application entry."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, replace

import tunes_player.gi_bootstrap  # noqa: F401 — before gi.repository
import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from tunes_player.core.logging_config import configure_logging
from tunes_player.core.models import Release, Source
from tunes_player.core.services import PlayerService
from tunes_player.core.shell_state import (
    SearchScope,
    ShellBase,
    ShellState,
    apply_shell_view_filters,
    cached_releases_compatible_with_available,
    cached_releases_have_quality_tiers,
    ensure_source_enabled,
    filter_releases_to_available_sources,
    prune_enabled_sources,
    refresh_local_peak_quality_tiers,
    genres_in_selection,
    prune_enabled_genres,
    prune_enabled_quality_tiers,
    quality_tiers_in_selection,
    release_to_cache_payload,
    releases_from_cache_payloads,
)
from tunes_player.platform.linux.audio import create_volume_controller
from tunes_player.ui.gtk.art import ArtLoader
from tunes_player.ui.gtk.errors import attach_error_toasts, show_error_toast
from tunes_player.ui.gtk.now_playing import NowPlayingBar, attach_media_keys
from tunes_player.ui.gtk.preferences import PreferencesWindow
from tunes_player.ui.gtk.shell_controller import (
    available_sources,
    empty_grid_message,
    fetch_base_releases,
    format_release_count_label,
)
from tunes_player.ui.gtk.genre_filter_menu import GenreFilterMenu
from tunes_player.ui.gtk.quality_multi_switch import QualityMultiSwitch
from tunes_player.ui.gtk.release_sort_switch import ReleaseSortSwitch
from tunes_player.ui.gtk.release_type_multi_switch import ReleaseTypeMultiSwitch
from tunes_player.ui.gtk.source_multi_switch import SourceMultiSwitch
from tunes_player.ui.gtk.util import escape_markup, load_app_css, source_label
from tunes_player.ui.gtk.views import (
    LoadingDiscoverView,
    PlaceholderView,
    QueueSheet,
    ReleaseDetailView,
    ReleaseGridView,
)
from tunes_player.ui.gtk.album_grid import ALBUM_GRID_VIEW_MARGIN, album_grid_min_content_width

_APP_WINDOW_TITLE = "Tunes Player"
_DEFAULT_SIZE = (960, 640)
_GRID_ROOT_TAG = "grid-root"
_PERSIST_DEBOUNCE_MS = 400
_ONBOARDING_MESSAGE = (
    "Configure music sources in Settings, then search or choose New Releases."
)
_PRESET_LABELS = {
    ShellBase.NEW_MUSIC: "New Releases",
    ShellBase.SUGGESTION: "Suggest Music",
    ShellBase.ALL_LOCAL: "All Local",
}
_LOADING_MESSAGES = {
    ShellBase.NEW_MUSIC: "Loading new releases…",
    ShellBase.SUGGESTION: "Finding suggestions...",
    ShellBase.ALL_LOCAL: "Loading local library…",
}

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _SelectionSnapshot:
    """Shell state plus unfiltered cache for selection-history Back."""

    state: ShellState
    releases: tuple[Release, ...]


class TunesWindow(Adw.ApplicationWindow):
    def __init__(self, *, application: Adw.Application, service: PlayerService) -> None:
        super().__init__(application=application, title=_APP_WINDOW_TITLE)
        self._service = service
        self._art_loader = ArtLoader(service.config.data_dir)
        self._shell_state = self._load_initial_shell_state()
        self._load_token = 0
        self._persist_timeout_id = 0
        self._updating_chips = False
        self._preferences: PreferencesWindow | None = None
        self._queue_sheet: QueueSheet | None = None
        self._source_multi: SourceMultiSwitch | None = None
        self._genre_filter: GenreFilterMenu | None = None
        self._release_type_multi: ReleaseTypeMultiSwitch | None = None
        self._quality_filter_multi: QualityMultiSwitch | None = None
        self._sort_switch: ReleaseSortSwitch | None = None
        self._cached_selection_key: tuple[str, str] | None = None
        self._cached_releases: list[Release] = []
        self._selection_stack: list[_SelectionSnapshot] = []
        self._prepared_for_first_show = False
        self.set_default_size(*_DEFAULT_SIZE)
        self.set_icon_name("tunes-player")

        self._now_playing = NowPlayingBar(service=service, art_loader=self._art_loader)
        self._now_playing.set_queue_handler(self._open_queue_sheet)
        self._now_playing.set_play_handler(self._on_play_clicked)
        self._now_playing.set_art_click_handler(self._open_current_release)

        self._toolbar = Adw.ToolbarView()
        self._toolbar.set_size_request(-1, -1)
        self._toolbar.add_bottom_bar(self._now_playing)
        self.set_content(self._toolbar)

        self._toast_overlay = Adw.ToastOverlay()
        self._toast_overlay.set_size_request(-1, -1)
        self._build_shell()
        attach_media_keys(self, service)
        attach_error_toasts(self._toast_overlay, service)
        service.subscribe(lambda event: GLib.idle_add(self._on_service_event, event))
        self.connect("close-request", self._on_close_request)

    def prepare_for_first_show(self) -> None:
        """Build the initial browse view before the window is first shown."""
        if self._prepared_for_first_show:
            return
        self._prepared_for_first_show = True
        if self.get_realized() and not self.get_mapped():
            self._apply_startup_window_size(*_DEFAULT_SIZE)
        self._reload_grid()
        self._ensure_startup_window_size()
        self._sync_visible_grid_layout()
        self._startup_playback_probe()

    def _apply_startup_window_size(self, width: int, height: int) -> None:
        """GTK 4 has no resize(); set default size and allocate before first map."""
        self.set_default_size(width, height)
        if not self.get_realized() or self.get_mapped():
            return
        allocation = Gdk.Rectangle()
        allocation.width = width
        allocation.height = height
        self.size_allocate(allocation, -1)

    def _ensure_startup_window_size(self) -> None:
        """Set the first mapped size from realized chrome, before present()."""
        if not self.get_realized():
            return
        default_w, default_h = _DEFAULT_SIZE
        width = default_w
        height = default_h
        for widget in (self._shell_controls, self._now_playing, self._header):
            _min_w, natural_w, _min_b, _nat_b = widget.measure(
                Gtk.Orientation.HORIZONTAL,
                height,
            )
            width = max(width, natural_w)
            _min_h, natural_h, _min_b, _nat_b = widget.measure(
                Gtk.Orientation.VERTICAL,
                width,
            )
            height = max(height, natural_h)
        self._apply_startup_window_size(width, height)

    def _sync_visible_grid_layout(self) -> None:
        page = self._main_nav.get_visible_page()
        if page is None:
            return
        child = page.get_child()
        if isinstance(child, ReleaseGridView):
            child.sync_tile_layout()

    def _load_initial_shell_state(self) -> ShellState:
        state = self._service.config.config.shell_state
        if not available_sources(self._service):
            return ShellState()
        sources = available_sources(self._service)
        pruned_sources = prune_enabled_sources(state.enabled_sources, sources)
        if pruned_sources != state.enabled_sources:
            state = replace(state, enabled_sources=pruned_sources)
        if state.base == ShellBase.ALL_LOCAL:
            if Source.LOCAL not in sources:
                state = replace(state, base=ShellBase.NONE, cached_releases=())
            else:
                state = replace(
                    state,
                    enabled_sources=ensure_source_enabled(
                        state.enabled_sources,
                        Source.LOCAL,
                        available=sources,
                    ),
                )
        return state

    def _build_shell(self) -> None:
        self._header = Adw.HeaderBar()
        self._toolbar.add_top_bar(self._header)

        self._window_title = Adw.WindowTitle(title=_APP_WINDOW_TITLE, subtitle="")
        title_center = Gtk.Box()
        title_center.set_halign(Gtk.Align.CENTER)
        title_center.append(self._window_title)
        self._header.set_title_widget(title_center)

        self._back_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self._back_btn.set_tooltip_text("Back")
        self._back_btn.set_visible(False)
        self._back_btn.connect("clicked", self._on_nav_back)
        self._header.pack_start(self._back_btn)

        settings_btn = Gtk.Button(icon_name="emblem-system-symbolic")
        settings_btn.set_tooltip_text("Settings")
        settings_btn.connect("clicked", self._open_preferences)
        self._header.pack_end(settings_btn)

        shell_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        shell_column.set_vexpand(True)
        shell_column.set_hexpand(True)

        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        controls.add_css_class("shell-controls")
        controls.set_margin_top(8)
        controls.set_margin_bottom(4)
        controls.set_margin_start(12)
        controls.set_margin_end(12)

        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        search_row.add_css_class("linked")
        search_row.add_css_class("shell-search-row")
        search_row.set_hexpand(True)
        self._search_entry = Gtk.SearchEntry()
        self._search_entry.add_css_class("shell-search-entry")
        self._search_entry.set_placeholder_text("Search releases…")
        self._search_entry.set_hexpand(True)
        self._search_entry.connect("activate", self._on_search_activate)
        search_row.append(self._search_entry)

        self._new_music_btn = Gtk.ToggleButton(label="New Releases")
        self._new_music_btn.add_css_class("shell-preset-btn")
        self._new_music_btn.connect("toggled", self._on_new_music_toggled)
        search_row.append(self._new_music_btn)

        self._suggestion_btn = Gtk.ToggleButton(label="Suggest Music")
        self._suggestion_btn.add_css_class("shell-preset-btn")
        self._suggestion_btn.connect("toggled", self._on_suggestion_toggled)
        search_row.append(self._suggestion_btn)

        self._all_local_btn = Gtk.ToggleButton(label="All Local")
        self._all_local_btn.add_css_class("shell-preset-btn")
        self._all_local_btn.connect("toggled", self._on_all_local_toggled)
        search_row.append(self._all_local_btn)

        controls.append(search_row)

        filter_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        filter_row.add_css_class("shell-filter-row")

        self._source_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._source_row.add_css_class("shell-source-row")
        self._source_row.set_hexpand(False)
        self._source_row.set_halign(Gtk.Align.START)
        filter_row.append(self._source_row)

        self._release_type_slot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._release_type_slot.add_css_class("shell-release-type-slot")
        self._release_type_slot.set_visible(False)
        self._release_type_slot.set_hexpand(False)
        self._release_type_slot.set_halign(Gtk.Align.START)
        self._release_type_slot.set_margin_start(24)
        filter_row.append(self._release_type_slot)

        self._genre_filter_slot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._genre_filter_slot.add_css_class("shell-genre-filter-slot")
        self._genre_filter_slot.set_visible(False)
        self._genre_filter_slot.set_hexpand(False)
        self._genre_filter_slot.set_halign(Gtk.Align.START)
        self._genre_filter_slot.set_margin_start(24)
        filter_row.append(self._genre_filter_slot)

        self._quality_filter_slot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._quality_filter_slot.add_css_class("shell-quality-filter-slot")
        self._quality_filter_slot.set_visible(False)
        self._quality_filter_slot.set_hexpand(False)
        self._quality_filter_slot.set_halign(Gtk.Align.START)
        self._quality_filter_slot.set_margin_start(24)
        filter_row.append(self._quality_filter_slot)

        self._sort_slot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._sort_slot.add_css_class("shell-sort-slot")
        self._sort_slot.set_visible(False)
        self._sort_slot.set_hexpand(False)
        self._sort_slot.set_halign(Gtk.Align.START)
        self._sort_slot.set_margin_start(24)
        filter_row.append(self._sort_slot)

        self._release_count_label = Gtk.Label(label="", xalign=1)
        self._release_count_label.add_css_class("shell-source-heading")
        self._release_count_label.add_css_class("shell-release-count")
        self._release_count_label.set_hexpand(True)
        self._release_count_label.set_halign(Gtk.Align.END)
        self._release_count_label.set_valign(Gtk.Align.CENTER)
        self._release_count_label.set_visible(False)
        filter_row.append(self._release_count_label)

        controls.append(filter_row)

        self._shell_controls = controls
        shell_column.append(controls)

        self._main_nav = Adw.NavigationView()
        self._main_nav.set_vexpand(True)
        self._main_nav.set_hexpand(True)
        self._main_nav.connect("notify::visible-page", self._on_nav_visible_page_changed)
        shell_column.append(self._main_nav)

        self._toast_overlay.set_child(shell_column)
        self._toolbar.set_content(self._toast_overlay)

        self._apply_window_min_width()
        self._rebuild_source_filters()
        self._sync_shell_controls()

    def _apply_window_min_width(self) -> None:
        min_width = album_grid_min_content_width()
        _min_w, min_h = self.get_size_request()
        if min_h < 0:
            min_h = 400
        self.set_size_request(min_width, min_h)

    def _album_grid_inner_width(self) -> int:
        window_width = self.get_width()
        if window_width < 64:
            return 0
        return max(0, window_width - 2 * ALBUM_GRID_VIEW_MARGIN)

    def _effective_shell_state(self) -> ShellState:
        if not available_sources(self._service):
            return ShellState()
        return self._shell_state

    def _selection_cache_key(self, state: ShellState) -> tuple[str, str, str]:
        if state.base == ShellBase.SEARCH:
            return (
                state.base.value,
                state.search_query.strip(),
                state.search_scope.value,
            )
        return (state.base.value, "", SearchScope.ALL.value)

    def _selection_identity_changed(self, previous: ShellState, current: ShellState) -> bool:
        if previous.base != current.base:
            return True
        if current.base == ShellBase.SEARCH:
            if previous.search_query != current.search_query:
                return True
            if previous.search_scope != current.search_scope:
                return True
        return False

    def _cache_matches(self, state: ShellState) -> bool:
        return (
            self._cached_selection_key is not None
            and self._cached_selection_key == self._selection_cache_key(state)
        )

    def _invalidate_selection_cache(self, *, clear_persisted: bool = False) -> None:
        self._cached_selection_key = None
        self._cached_releases = []
        if clear_persisted and self._shell_state.cached_releases:
            self._shell_state = replace(self._shell_state, cached_releases=())

    def _store_selection_cache(self, state: ShellState, releases: list[Release]) -> None:
        self._cached_selection_key = self._selection_cache_key(state)
        self._cached_releases = list(releases)
        self._sync_release_type_multi()
        self._sync_genre_filter()
        self._sync_quality_filter()
        self._sync_sort_switch()

    def _available_sources(self) -> frozenset[Source]:
        return frozenset(available_sources(self._service))

    def _filtered_from_cache(self, state: ShellState) -> list[Release]:
        return apply_shell_view_filters(
            self._cached_releases,
            enabled_sources=state.enabled_sources,
            enabled_genres=state.enabled_genres,
            enabled_release_types=state.enabled_release_types,
            enabled_quality_tiers=state.enabled_quality_tiers,
            available_sources=self._available_sources(),
            sort_key=state.sort_key,
            sort_descending=state.sort_descending,
        )

    def _display_cached_selection(self, state: ShellState | None = None) -> None:
        state = state or self._effective_shell_state()
        if not self._cache_matches(state):
            self._reload_grid()
            return
        releases = self._filtered_from_cache(state)
        self._show_grid(
            releases=releases,
            empty_message=self._empty_message(state, releases),
            title=self._grid_title(state),
            sync_populate=True,
        )

    def _set_shell_state(
        self,
        state: ShellState,
        *,
        reload: bool = True,
        clear_selection_history: bool = True,
        restoring_history: bool = False,
    ) -> None:
        identity_changed = self._selection_identity_changed(self._shell_state, state)
        if identity_changed and clear_selection_history:
            self._clear_selection_history()
        if identity_changed and not restoring_history:
            state = replace(
                state,
                enabled_genres=frozenset(),
                sort_key=None,
                cached_releases=(),
            )
        self._shell_state = state
        self._sync_shell_controls()
        self._schedule_persist()
        if reload:
            if identity_changed:
                self._invalidate_selection_cache()
                self._reload_grid()
            else:
                self._display_cached_selection(state)
        self._sync_header_with_nav()

    def _sync_shell_controls(self) -> None:
        state = self._shell_state
        self._updating_preset = True
        try:
            self._new_music_btn.set_active(state.base == ShellBase.NEW_MUSIC)
            self._suggestion_btn.set_active(state.base == ShellBase.SUGGESTION)
            self._all_local_btn.set_active(state.base == ShellBase.ALL_LOCAL)
        finally:
            self._updating_preset = False

        if state.base == ShellBase.SEARCH:
            current = self._search_entry.get_text()
            if current != state.search_query:
                self._search_entry.set_text(state.search_query)
        elif state.base in (ShellBase.NEW_MUSIC, ShellBase.SUGGESTION, ShellBase.ALL_LOCAL):
            if self._search_entry.get_text():
                self._search_entry.set_text("")

        self._all_local_btn.set_visible(Source.LOCAL in available_sources(self._service))
        self._sync_source_multi()
        self._sync_release_type_multi()
        self._sync_genre_filter()
        self._sync_quality_filter()
        self._sync_sort_switch()

    def _sync_sort_switch(self) -> None:
        show = bool(self._cached_releases)
        self._sort_slot.set_visible(show)
        if not show:
            return
        state = self._shell_state
        if self._sort_switch is None:
            self._sort_switch = ReleaseSortSwitch(
                sort_key=state.sort_key,
                sort_descending=state.sort_descending,
                on_changed=self._on_sort_changed,
            )
            self._sort_slot.append(self._sort_switch)
        else:
            self._sort_switch.set_sort_state(state.sort_key, state.sort_descending)

    def _on_sort_changed(self, sort_key: str | None, sort_descending: bool) -> None:
        self._set_shell_sort(sort_key, sort_descending)

    def _set_shell_sort(self, sort_key: str | None, sort_descending: bool) -> None:
        state = self._shell_state
        if state.sort_key == sort_key and state.sort_descending == sort_descending:
            return
        self._shell_state = replace(
            state,
            sort_key=sort_key,
            sort_descending=sort_descending,
        )
        if self._sort_switch is not None:
            self._sort_switch.set_sort_state(sort_key, sort_descending)
        self._schedule_persist()
        self._display_cached_selection()

    def _sync_release_type_multi(self) -> None:
        show = bool(self._cached_releases)
        self._release_type_slot.set_visible(show)
        if not show:
            return
        if self._release_type_multi is None:
            self._release_type_multi = ReleaseTypeMultiSwitch(
                enabled_release_types=self._shell_state.enabled_release_types,
                on_changed=self._on_release_type_multi_changed,
            )
            self._release_type_slot.append(self._release_type_multi)
        else:
            self._release_type_multi.set_enabled_release_types(
                self._shell_state.enabled_release_types,
            )

    def _on_release_type_multi_changed(self, enabled_release_types: frozenset[str]) -> None:
        self._set_enabled_release_types(enabled_release_types)

    def _set_enabled_release_types(self, enabled_release_types: frozenset[str]) -> None:
        state = self._shell_state
        if state.enabled_release_types == enabled_release_types:
            return
        self._shell_state = replace(
            state,
            enabled_release_types=enabled_release_types,
        )
        if self._release_type_multi is not None:
            self._release_type_multi.set_enabled_release_types(enabled_release_types)
        self._schedule_persist()
        self._display_cached_selection()

    def _sync_quality_filter(self) -> None:
        available = quality_tiers_in_selection(self._cached_releases)
        show = bool(self._cached_releases)
        state = self._shell_state
        pruned = prune_enabled_quality_tiers(state.enabled_quality_tiers, available)
        if pruned != state.enabled_quality_tiers:
            self._shell_state = replace(state, enabled_quality_tiers=pruned)
            state = self._shell_state

        self._quality_filter_slot.set_visible(show)
        if not show:
            return

        if self._quality_filter_multi is None:
            self._quality_filter_multi = QualityMultiSwitch(
                enabled_quality_tiers=state.enabled_quality_tiers,
                on_changed=self._on_quality_filter_changed,
            )
            self._quality_filter_slot.append(self._quality_filter_multi)
        else:
            self._quality_filter_multi.set_enabled_quality_tiers(
                state.enabled_quality_tiers,
            )

    def _on_quality_filter_changed(self, enabled_quality_tiers: frozenset[str]) -> None:
        self._set_enabled_quality_tiers(enabled_quality_tiers)

    def _set_enabled_quality_tiers(self, enabled_quality_tiers: frozenset[str]) -> None:
        state = self._shell_state
        if state.enabled_quality_tiers == enabled_quality_tiers:
            return
        self._shell_state = replace(state, enabled_quality_tiers=enabled_quality_tiers)
        if self._quality_filter_multi is not None:
            self._quality_filter_multi.set_enabled_quality_tiers(enabled_quality_tiers)
        self._schedule_persist()
        self._display_cached_selection()

    def _sync_genre_filter(self) -> None:
        available = genres_in_selection(self._cached_releases)
        show = bool(self._cached_releases)
        state = self._shell_state
        pruned = prune_enabled_genres(state.enabled_genres, available)
        if pruned != state.enabled_genres:
            self._shell_state = replace(state, enabled_genres=pruned)
            state = self._shell_state

        self._genre_filter_slot.set_visible(show)
        if not show:
            return

        if self._genre_filter is None:
            self._genre_filter = GenreFilterMenu(
                on_changed=self._on_genre_filter_changed,
            )
            self._genre_filter.set_hexpand(False)
            self._genre_filter_slot.append(self._genre_filter)
        self._genre_filter.set_genres(available, state.enabled_genres)

    def _on_genre_filter_changed(self, enabled_genres: frozenset[str]) -> None:
        self._set_enabled_genres(enabled_genres)

    def _set_enabled_genres(self, enabled_genres: frozenset[str]) -> None:
        state = self._shell_state
        if state.enabled_genres == enabled_genres:
            return
        self._shell_state = replace(state, enabled_genres=enabled_genres)
        if self._genre_filter is not None:
            self._genre_filter.set_enabled_genres(enabled_genres)
        self._schedule_persist()
        self._display_cached_selection()

    def _rebuild_source_filters(self) -> None:
        sources = available_sources(self._service)
        pruned = prune_enabled_sources(self._shell_state.enabled_sources, sources)
        if pruned != self._shell_state.enabled_sources:
            self._shell_state = replace(self._shell_state, enabled_sources=pruned)
        if len(sources) <= 1:
            self._source_row.set_visible(False)
            if self._shell_state.enabled_sources:
                self._shell_state = replace(
                    self._shell_state,
                    enabled_sources=frozenset(),
                )
            self._source_multi = None
            child = self._source_row.get_first_child()
            while child is not None:
                next_child = child.get_next_sibling()
                self._source_row.remove(child)
                child = next_child
            return

        self._source_row.set_visible(True)
        if self._source_multi is None:
            self._source_multi = SourceMultiSwitch(
                sources=sources,
                enabled_sources=self._shell_state.enabled_sources,
                on_changed=self._on_source_multi_changed,
            )
            self._source_row.append(self._source_multi)
        else:
            self._source_multi.set_sources(sources, self._shell_state.enabled_sources)

    def _sync_source_multi(self) -> None:
        if self._source_multi is not None:
            self._source_multi.set_enabled_sources(self._shell_state.enabled_sources)

    def _on_source_multi_changed(self, enabled_sources: frozenset[Source]) -> None:
        self._set_enabled_sources(enabled_sources)

    def _set_enabled_sources(self, enabled_sources: frozenset[Source]) -> None:
        state = self._shell_state
        if state.enabled_sources == enabled_sources:
            return
        self._shell_state = replace(state, enabled_sources=enabled_sources)
        self._sync_source_multi()
        self._schedule_persist()
        self._display_cached_selection()

    def _on_new_music_toggled(self, button: Gtk.ToggleButton) -> None:
        if getattr(self, "_updating_preset", False):
            return
        if button.get_active():
            self._activate_preset(ShellBase.NEW_MUSIC)
        elif self._shell_state.base == ShellBase.NEW_MUSIC:
            button.set_active(True)

    def _on_suggestion_toggled(self, button: Gtk.ToggleButton) -> None:
        if getattr(self, "_updating_preset", False):
            return
        if button.get_active():
            self._activate_preset(ShellBase.SUGGESTION)
        elif self._shell_state.base == ShellBase.SUGGESTION:
            button.set_active(True)

    def _on_all_local_toggled(self, button: Gtk.ToggleButton) -> None:
        if getattr(self, "_updating_preset", False):
            return
        if button.get_active():
            self._activate_preset(ShellBase.ALL_LOCAL)
        elif self._shell_state.base == ShellBase.ALL_LOCAL:
            button.set_active(True)

    def _commit_selection_change(
        self,
        next_state: ShellState,
        *,
        skip_history_push: bool = False,
    ) -> None:
        if (
            not skip_history_push
            and self._selection_identity_changed(self._shell_state, next_state)
        ):
            self._push_selection_history()
        if not self._nav_at_root():
            self._main_nav.pop()
        self._set_shell_state(next_state, clear_selection_history=False)

    def _activate_preset(self, base: ShellBase) -> None:
        enabled_sources = self._shell_state.enabled_sources
        if base == ShellBase.ALL_LOCAL:
            enabled_sources = ensure_source_enabled(
                enabled_sources,
                Source.LOCAL,
                available=available_sources(self._service),
            )
        next_state = replace(
            self._shell_state,
            base=base,
            search_query="",
            search_scope=SearchScope.ALL,
            enabled_sources=enabled_sources,
            cached_releases=(),
        )
        self._commit_selection_change(next_state)

    def _on_search_activate(self, entry: Gtk.SearchEntry) -> None:
        self._navigate_to_search(entry.get_text())

    def _navigate_to_search(
        self,
        query: str,
        *,
        search_scope: SearchScope = SearchScope.ALL,
    ) -> None:
        """Run a search query, keeping prior results on the selection Back stack."""
        text = query.strip()
        if not text:
            return
        next_state = replace(
            self._shell_state,
            base=ShellBase.SEARCH,
            search_query=text,
            search_scope=search_scope,
            cached_releases=(),
        )
        same_query = (
            self._shell_state.base == ShellBase.SEARCH
            and self._shell_state.search_query.strip() == text
            and self._shell_state.search_scope == search_scope
        )
        self._search_entry.set_text(text)
        self._commit_selection_change(next_state, skip_history_push=same_query)

    def _schedule_persist(self) -> None:
        if self._persist_timeout_id:
            GLib.source_remove(self._persist_timeout_id)
        self._persist_timeout_id = GLib.timeout_add(
            _PERSIST_DEBOUNCE_MS,
            self._persist_shell_state,
        )

    def _shell_state_for_persist(self) -> ShellState:
        state = self._shell_state
        cached_payloads: tuple[dict, ...] = ()
        if self._cache_matches(state) and self._cached_releases:
            cached_payloads = tuple(
                release_to_cache_payload(release) for release in self._cached_releases
            )
        return replace(
            state,
            cached_releases=cached_payloads,
        )

    def _persist_shell_state(self) -> bool:
        self._persist_timeout_id = 0
        self._service.config.set_shell_state(self._shell_state_for_persist())
        return False

    def _on_close_request(self, *_args: object) -> bool:
        if self._persist_timeout_id:
            GLib.source_remove(self._persist_timeout_id)
            self._persist_timeout_id = 0
        self._service.config.set_shell_state(self._shell_state_for_persist())
        return False

    def _refresh_cached_release_quality(self, releases: list[Release]) -> list[Release]:
        if not any(release.source == Source.LOCAL for release in releases):
            return releases
        local_tier_by_id = {
            release.id: release.peak_quality_tier
            for release in self._service.list_releases()
        }
        return refresh_local_peak_quality_tiers(
            releases,
            local_tier_by_id=local_tier_by_id,
        )

    def _persisted_grid_cache_stale(self, state: ShellState) -> bool:
        if state.base != ShellBase.ALL_LOCAL:
            return False
        if not self._service.config.config.music_folders:
            return False
        cached_count = len(state.cached_releases)
        if cached_count == 0:
            return False
        return cached_count != self._service.store.release_count()

    def _restore_persisted_releases(self, state: ShellState) -> list[Release] | None:
        """Deserialize persisted grid rows when still valid (safe off the UI thread)."""
        if state.base == ShellBase.NONE or not state.cached_releases:
            return None
        available = self._available_sources()
        if state.base == ShellBase.ALL_LOCAL and Source.LOCAL not in available:
            return None
        if self._persisted_grid_cache_stale(state):
            return None
        if not cached_releases_compatible_with_available(state.cached_releases, available):
            return None
        releases = releases_from_cache_payloads(state.cached_releases)
        if not releases:
            return None
        releases = filter_releases_to_available_sources(releases, available)
        if not releases:
            return None
        if not cached_releases_have_quality_tiers(state.cached_releases):
            releases = self._refresh_cached_release_quality(releases)
        return releases

    def _load_releases_for_state(self, state: ShellState) -> list[Release]:
        restored = self._restore_persisted_releases(state)
        if restored is not None:
            return restored
        return fetch_base_releases(
            self._service,
            state.base,
            search_query=state.search_query,
            search_scope=state.search_scope,
        )

    def _show_grid_for_state(self, state: ShellState, releases: list[Release]) -> None:
        self._store_selection_cache(state, releases)
        filtered = self._filtered_from_cache(self._shell_state)
        self._show_grid(
            releases=filtered,
            empty_message=self._empty_message(self._shell_state, filtered),
            title=self._grid_title(self._shell_state),
            sync_populate=True,
        )
        self._schedule_persist()

    def _try_show_grid_sync(self, state: ShellState) -> bool:
        restored = self._restore_persisted_releases(state)
        if restored is not None:
            self._show_grid_for_state(state, restored)
            return True
        if state.base == ShellBase.ALL_LOCAL:
            releases = fetch_base_releases(
                self._service,
                state.base,
                search_query=state.search_query,
                search_scope=state.search_scope,
            )
            self._show_grid_for_state(state, releases)
            return True
        return False

    def _reload_grid(self) -> bool:
        state = self._effective_shell_state()

        if state.base == ShellBase.NONE:
            self._store_selection_cache(state, [])
            self._show_grid(
                releases=[],
                empty_message=self._empty_message(state, []),
                title=self._grid_title(state),
                sync_populate=True,
            )
            return False

        if self._try_show_grid_sync(state):
            return False

        self._start_async_load(state)
        return False

    def _async_load_matches(self, request: ShellState) -> bool:
        current = self._shell_state
        if current.base != request.base:
            return False
        if request.base == ShellBase.SEARCH:
            return (
                current.search_query == request.search_query
                and current.search_scope == request.search_scope
            )
        return True

    def _start_async_load(self, state: ShellState) -> None:
        self._load_token += 1
        token = self._load_token
        self._show_grid_loading(state)

        def work() -> None:
            try:
                releases = self._load_releases_for_state(state)
                GLib.idle_add(self._finish_async_load, token, state, releases, None)
            except Exception as exc:
                log.exception("Shell load failed for %s", state.base.value)
                GLib.idle_add(self._finish_async_load, token, state, None, exc)

        threading.Thread(target=work, daemon=True).start()

    def _finish_async_load(
        self,
        token: int,
        request: ShellState,
        releases: list | None,
        error: BaseException | None,
    ) -> bool:
        if token != self._load_token:
            return False
        if not self._async_load_matches(request):
            return False

        title = self._grid_title(request)
        if error is not None:
            toast, message = self._async_load_error_copy(request, title=title)
            show_error_toast(self._toast_overlay, toast)
            view = PlaceholderView(title=title, message=message)
            self._replace_root_page(title=title, child=view)
            self._hide_release_count_label()
            return False

        loaded = releases or []
        self._store_selection_cache(self._shell_state, loaded)
        filtered = self._filtered_from_cache(self._shell_state)
        self._show_grid(
            releases=filtered,
            empty_message=self._empty_message(self._shell_state, filtered),
            title=title,
        )
        self._schedule_persist()
        return False

    def _async_load_error_copy(
        self,
        request: ShellState,
        *,
        title: str,
    ) -> tuple[str, str]:
        if request.base == ShellBase.SEARCH:
            return (
                "Search failed. Check your connection and sign-in.",
                "Could not complete search. Try again in a moment.",
            )
        if request.base == ShellBase.ALL_LOCAL:
            return (
                "Could not load your local library.",
                "Could not load All Local. Try again in a moment.",
            )
        return (
            f"Could not load {title}. Check your connection and sign-in.",
            f"Could not load {title}. Try again in a moment.",
        )

    def _loading_message(self, state: ShellState) -> str:
        if state.base == ShellBase.SEARCH and state.search_query.strip():
            return f'Searching for “{state.search_query}”…'
        title = _PRESET_LABELS.get(state.base, "Tunes")
        return _LOADING_MESSAGES.get(state.base, f"Loading {title}…")

    def _show_grid_loading(self, state: ShellState) -> None:
        title = self._grid_title(state)
        self._replace_root_page(
            title=title,
            child=LoadingDiscoverView(message=self._loading_message(state)),
        )
        self._sync_release_count_loading()

    def _sync_release_count_label(
        self,
        *,
        filtered_count: int,
        catalog_count: int | None = None,
    ) -> None:
        if self._shell_state.base == ShellBase.NONE:
            self._release_count_label.set_visible(False)
            return
        self._release_count_label.set_visible(True)
        self._release_count_label.set_label(
            format_release_count_label(
                filtered_count=filtered_count,
                catalog_count=catalog_count,
            )
        )

    def _sync_release_count_loading(self) -> None:
        if self._shell_state.base == ShellBase.NONE:
            self._release_count_label.set_visible(False)
            return
        self._release_count_label.set_visible(True)
        self._release_count_label.set_label("Loading…")

    def _hide_release_count_label(self) -> None:
        self._release_count_label.set_visible(False)

    def _show_grid(
        self,
        *,
        releases: list,
        empty_message: str | None,
        title: str,
        sync_populate: bool = False,
    ) -> None:
        view = ReleaseGridView(
            releases=releases,
            on_release_activated=self._open_release,
            on_release_play=lambda release_id: self._service.play_or_toggle_release(
                release_id, start_index=0
            ),
            on_artist_search=self._search_for_artist,
            empty_message=empty_message,
            art_loader=self._art_loader,
            window_inner_width_fn=self._album_grid_inner_width,
            service=self._service,
            sync_populate=sync_populate,
        )
        self._replace_root_page(title=title, child=view)
        catalog_count = (
            len(self._cached_releases)
            if self._cache_matches(self._shell_state)
            else None
        )
        self._sync_release_count_label(
            filtered_count=len(releases),
            catalog_count=catalog_count,
        )

    def _catalog_releases_for_message(self, state: ShellState) -> list[Release]:
        if self._cache_matches(state) and self._cached_releases:
            return self._cached_releases
        return []

    def _empty_message(self, state: ShellState, releases: list) -> str | None:
        if releases:
            return None
        if not available_sources(self._service):
            return _ONBOARDING_MESSAGE
        if state.base == ShellBase.NONE:
            return _ONBOARDING_MESSAGE
        if state.base == ShellBase.SEARCH and not state.search_query.strip():
            return _ONBOARDING_MESSAGE
        catalog = self._catalog_releases_for_message(state)
        return empty_grid_message(
            self._service,
            state,
            catalog_count=len(catalog),
        )

    def _grid_title(self, state: ShellState) -> str:
        if state.base == ShellBase.SEARCH and state.search_query.strip():
            return state.search_query
        return _PRESET_LABELS.get(state.base, "Tunes")

    def _replace_root_page(self, *, title: str, child: Gtk.Widget) -> None:
        self._pop_to_root()
        current = self._main_nav.get_visible_page()
        if current is not None and current.get_tag() == _GRID_ROOT_TAG:
            current.set_child(child)
            current.set_title(escape_markup(title))
            return
        self._main_nav.add(
            Adw.NavigationPage(
                title=escape_markup(title),
                child=child,
                tag=_GRID_ROOT_TAG,
            ),
        )

    def _release_id_for_current_view(self) -> str | None:
        page = self._main_nav.get_visible_page()
        if page is None:
            return None
        tag = page.get_tag()
        if not tag or tag == _GRID_ROOT_TAG:
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

    def _open_current_release(self) -> None:
        state = self._service.get_playback_state()
        track = state.current_track
        if track is None:
            return
        release_id = self._service.release_id_for_track(track)
        if release_id is None:
            return
        self._open_release(release_id)

    def _open_release(self, release_id: str) -> None:
        release = self._service.get_release(release_id)
        if release is None:
            return
        detail = ReleaseDetailView(
            service=self._service,
            release=release,
            art_loader=self._art_loader,
            on_artist_search=self._search_for_artist,
        )
        page = Adw.NavigationPage(
            title=escape_markup(release.title),
            child=detail,
            tag=release_id,
        )
        self._pop_to_root()
        self._main_nav.push(page)
        self._sync_header_with_nav()

    def _clear_selection_history(self) -> None:
        self._selection_stack.clear()

    def _capture_selection_snapshot(self) -> _SelectionSnapshot:
        state = self._shell_state
        if self._cache_matches(state) and self._cached_releases:
            payloads = tuple(
                release_to_cache_payload(release) for release in self._cached_releases
            )
            state = replace(state, cached_releases=payloads)
        return _SelectionSnapshot(state=state, releases=tuple(self._cached_releases))

    def _push_selection_history(self) -> None:
        self._selection_stack.append(self._capture_selection_snapshot())

    def _restore_selection_snapshot(self, snapshot: _SelectionSnapshot) -> None:
        releases = list(snapshot.releases)
        if not releases and snapshot.state.cached_releases:
            releases = releases_from_cache_payloads(snapshot.state.cached_releases)
        self._shell_state = snapshot.state
        self._sync_shell_controls()
        if releases:
            self._store_selection_cache(snapshot.state, releases)
        else:
            self._invalidate_selection_cache()
        self._display_cached_selection()
        self._schedule_persist()
        self._sync_header_with_nav()

    def _search_for_artist(self, artist_name: str) -> None:
        self._navigate_to_search(artist_name, search_scope=SearchScope.ARTIST)

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
            GLib.idle_add(self._on_library_updated)
        elif event == "sources_changed":
            GLib.idle_add(self._on_sources_or_library_changed)
        elif event == "art_updated":
            GLib.idle_add(self._on_art_updated)
        return False

    def _on_art_updated(self) -> bool:
        self._refresh_cached_local_art()
        page = self._main_nav.get_visible_page()
        if page is not None:
            child = page.get_child()
            if isinstance(child, ReleaseGridView):
                child.refresh_artwork(self._service.store)
        return False

    def _refresh_cached_local_art(self) -> None:
        if not self._cached_releases:
            return
        local_ids = [release.id for release in self._cached_releases if release.source == Source.LOCAL]
        if not local_ids:
            return
        art_by_id = self._service.store.art_uri_map(local_ids)
        self._cached_releases = [
            replace(release, art_uri=art_by_id.get(release.id))
            if release.source == Source.LOCAL
            else release
            for release in self._cached_releases
        ]

    def _on_sources_or_library_changed(self) -> bool:
        self._rebuild_source_filters()
        self._sync_shell_controls()
        self._invalidate_selection_cache(clear_persisted=True)
        self._reload_grid()
        return False

    def _on_library_updated(self) -> bool:
        if not self._nav_at_root():
            return False

        state = self._effective_shell_state()
        if state.base == ShellBase.ALL_LOCAL:
            releases = fetch_base_releases(
                self._service,
                state.base,
                search_query=state.search_query,
                search_scope=state.search_scope,
            )
            self._store_selection_cache(state, releases)
            if self._cache_matches(state):
                self._display_cached_selection(state)
                self._schedule_persist()
            return False

        if self._cached_releases and any(
            release.source == Source.LOCAL for release in self._cached_releases
        ):
            refreshed = self._refresh_cached_release_quality(list(self._cached_releases))
            self._store_selection_cache(state, refreshed)
            if self._cache_matches(state):
                self._display_cached_selection(state)
                self._schedule_persist()
        return False

    def _open_queue_sheet(self, *_args: object) -> None:
        if self._queue_sheet is None:
            self._queue_sheet = QueueSheet(service=self._service)
            self._queue_sheet.connect("closed", lambda *_: setattr(self, "_queue_sheet", None))
        self._queue_sheet.present(self)

    def _pop_to_root(self) -> None:
        page = self._main_nav.get_visible_page()
        while page is not None and page.get_tag() != _GRID_ROOT_TAG:
            self._main_nav.pop()
            page = self._main_nav.get_visible_page()

    def _nav_at_root(self) -> bool:
        page = self._main_nav.get_visible_page()
        if page is None:
            return True
        return page.get_tag() == _GRID_ROOT_TAG

    def _can_go_back(self) -> bool:
        return not self._nav_at_root() or bool(self._selection_stack)

    def _sync_header_with_nav(self) -> None:
        at_root = self._nav_at_root()
        self._shell_controls.set_visible(at_root)
        self._back_btn.set_visible(self._can_go_back())

    def _on_nav_back(self, *_args: object) -> None:
        if not self._nav_at_root():
            self._main_nav.pop()
            self._sync_header_with_nav()
            return
        if self._selection_stack:
            self._restore_selection_snapshot(self._selection_stack.pop())

    def _on_nav_visible_page_changed(self, *_args: object) -> None:
        self._sync_header_with_nav()


def run() -> int:
    from tunes_player.core.config import ConfigManager

    load_app_css()
    Gtk.Window.set_default_icon_name("tunes-player")
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
                if not window.get_realized():
                    window.realize()
                window.prepare_for_first_show()
            window.present()

        def do_shutdown(self) -> None:  # noqa: N802 — GTK vfunc
            nonlocal mpris_service, folder_monitor
            folder_monitor.stop()
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

    from tunes_player.ui.gtk.folder_monitor import FolderMonitorManager

    folder_monitor = FolderMonitorManager(service)
    folder_monitor.start()

    def _poll_playback() -> bool:
        service.poll_playback()
        return True

    def _poll_scan() -> bool:
        service.poll_scan()
        return True

    GLib.timeout_add(100, _poll_playback)
    GLib.timeout_add(200, _poll_scan)
    return app.run(None)
