"""Genre filter: menu button with checkmark list in a popover."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk  # noqa: E402

EnabledGenresChanged = Callable[[frozenset[str]], None]

_SEARCH_MIN_GENRES = 10
_POPOVER_MAX_HEIGHT = 320
_LIST_WIDTH = 240


class GenreFilterMenu(Gtk.Box):
    """Heading plus menu button; popover holds searchable checkmark list."""

    def __init__(self, *, on_changed: EnabledGenresChanged | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.add_css_class("genre-filter-row")
        self.add_css_class("shell-source-multi")
        self.set_valign(Gtk.Align.CENTER)
        self.set_hexpand(False)
        self.set_halign(Gtk.Align.START)

        self._on_changed = on_changed
        self._updating = False
        self._genres: tuple[str, ...] = ()
        self._checks: dict[str, Gtk.CheckButton] = {}
        self._rows: dict[str, Gtk.ListBoxRow] = {}

        heading = Gtk.Label(label="Genre")
        heading.add_css_class("shell-source-heading")
        heading.set_halign(Gtk.Align.START)
        heading.set_valign(Gtk.Align.CENTER)
        heading.set_hexpand(False)
        heading.set_margin_end(0)
        self.append(heading)

        self._menu_label = Gtk.Label(label="All genres")
        self._menu_label.add_css_class("shell-source-btn-label")
        self._menu_label.set_halign(Gtk.Align.START)
        self._menu_label.set_hexpand(False)

        # Gtk.Button (not MenuButton) so shell-source-btn CSS applies.
        self._menu_btn = Gtk.Button()
        self._menu_btn.add_css_class("flat")
        self._menu_btn.add_css_class("shell-source-btn")
        self._menu_btn.set_child(self._menu_label)
        self._menu_btn.set_valign(Gtk.Align.CENTER)
        self._menu_btn.set_margin_top(0)
        self._menu_btn.set_margin_bottom(0)
        self._menu_btn.set_hexpand(False)
        self._menu_btn.set_halign(Gtk.Align.START)
        self._menu_btn.set_size_request(-1, 18)
        self._menu_btn.connect("clicked", self._on_menu_btn_clicked)
        self.append(self._menu_btn)

        popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        popover_box.set_margin_top(4)
        popover_box.set_margin_bottom(4)
        popover_box.set_margin_start(6)
        popover_box.set_margin_end(6)
        popover_box.set_size_request(_LIST_WIDTH, -1)

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text("Filter genres…")
        self._search_entry.set_visible(False)
        self._search_entry.connect("search-changed", self._on_search_changed)
        popover_box.append(self._search_entry)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_max_content_height(_POPOVER_MAX_HEIGHT)
        scrolled.set_vexpand(True)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list.add_css_class("genre-filter-list")
        scrolled.set_child(self._list)
        popover_box.append(scrolled)

        clear_btn = Gtk.Button(label="Clear selection")
        clear_btn.add_css_class("flat")
        clear_btn.add_css_class("genre-filter-clear")
        clear_btn.set_halign(Gtk.Align.START)
        clear_btn.set_margin_top(4)
        clear_btn.connect("clicked", self._on_clear_clicked)
        popover_box.append(clear_btn)

        self._popover = Gtk.Popover()
        self._popover.add_css_class("genre-filter-popover")
        self._popover.set_child(popover_box)
        self._popover.set_parent(self._menu_btn)

    def set_genres(
        self,
        genres: tuple[str, ...],
        enabled_genres: frozenset[str],
    ) -> None:
        self._genres = genres
        self._rebuild_list()
        self.set_enabled_genres(enabled_genres)
        self._search_entry.set_visible(len(genres) >= _SEARCH_MIN_GENRES)
        if not self._search_entry.get_visible():
            self._search_entry.set_text("")
            self._apply_search_filter("")

    def set_enabled_genres(self, enabled_genres: frozenset[str]) -> None:
        self._updating = True
        try:
            for genre, check in self._checks.items():
                check.set_active(genre in enabled_genres)
        finally:
            self._updating = False
        self._update_menu_label(enabled_genres)

    def get_enabled_genres(self) -> frozenset[str]:
        return frozenset(
            genre for genre, check in self._checks.items() if check.get_active()
        )

    def _rebuild_list(self) -> None:
        child = self._list.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._list.remove(child)
            child = next_child
        self._checks.clear()
        self._rows.clear()

        for genre in self._genres:
            check = Gtk.CheckButton(label=genre)
            check.set_margin_start(2)
            check.set_margin_end(2)
            check.set_margin_top(1)
            check.set_margin_bottom(1)
            check.connect("toggled", self._on_check_toggled)

            row = Gtk.ListBoxRow()
            row.set_child(check)
            self._list.append(row)
            self._checks[genre] = check
            self._rows[genre] = row

    def _update_menu_label(self, enabled_genres: frozenset[str]) -> None:
        if not enabled_genres:
            self._menu_label.set_label("All genres")
            return
        ordered = sorted(enabled_genres, key=lambda item: item.casefold())
        if len(ordered) == 1:
            self._menu_label.set_label(ordered[0])
            return
        self._menu_label.set_label(f"{len(ordered)} selected")

    def _on_check_toggled(self, *_args: object) -> None:
        if self._updating:
            return
        enabled = self.get_enabled_genres()
        self._update_menu_label(enabled)
        if self._on_changed is not None:
            self._on_changed(enabled)

    def _on_menu_btn_clicked(self, *_args: object) -> None:
        self._popover.popup()

    def _on_clear_clicked(self, *_args: object) -> None:
        if not self.get_enabled_genres():
            return
        self.set_enabled_genres(frozenset())
        if self._on_changed is not None:
            self._on_changed(frozenset())

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._apply_search_filter(entry.get_text())

    def _apply_search_filter(self, query: str) -> None:
        needle = query.strip().casefold()
        for genre, row in self._rows.items():
            visible = not needle or needle in genre.casefold()
            row.set_visible(visible)
