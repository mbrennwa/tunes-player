"""Genre filter: menu button with checkmark list in a popover."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk  # noqa: E402

EnabledGenresChanged = Callable[[frozenset[str]], None]

_SEARCH_MIN_GENRES = 5
_POPOVER_LIST_HEIGHT_FALLBACK = 320
_POPOVER_MIN_LIST_HEIGHT = 120
_POPOVER_BOTTOM_MARGIN = 16
_POPOVER_TOP_MARGIN = 8
_POPOVER_BOX_MARGIN_VERTICAL = 8
_POPOVER_CSS_PADDING_VERTICAL = 8
_SEARCH_ENTRY_HEIGHT = 38
_CLEAR_BTN_HEIGHT = 38
_GENRE_ROW_HEIGHT = 32
_MENU_BTN_HEIGHT = 18
_LIST_WIDTH = 240
_WINDOW_HEIGHT_FRACTION = 0.65


def genre_filter_list_max_height(
    *,
    natural_list_height: int,
    available_height: int,
    min_list_height: int = _POPOVER_MIN_LIST_HEIGHT,
    fallback_max: int = _POPOVER_LIST_HEIGHT_FALLBACK,
) -> int:
    """Clamp genre list height to the space available for the scroll area."""
    if available_height <= 0:
        return fallback_max
    target = min(natural_list_height, available_height)
    if natural_list_height > target:
        return max(min_list_height, target)
    return target


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

        self._popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._popover_box.set_margin_top(4)
        self._popover_box.set_margin_bottom(4)
        self._popover_box.set_margin_start(6)
        self._popover_box.set_margin_end(6)
        self._popover_box.set_size_request(_LIST_WIDTH, -1)

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text("Filter genres…")
        self._search_entry.set_visible(False)
        self._search_entry.set_hexpand(True)
        self._search_entry.set_margin_bottom(4)
        self._search_entry.connect("search-changed", self._on_search_changed)
        self._popover_box.append(self._search_entry)

        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scrolled.set_max_content_height(_POPOVER_LIST_HEIGHT_FALLBACK)
        self._scrolled.set_vexpand(False)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list.add_css_class("genre-filter-list")
        self._scrolled.set_child(self._list)
        self._popover_box.append(self._scrolled)

        self._clear_btn = Gtk.Button(label="Clear selection")
        self._clear_btn.add_css_class("flat")
        self._clear_btn.add_css_class("genre-filter-clear")
        self._clear_btn.set_halign(Gtk.Align.START)
        self._clear_btn.set_margin_top(4)
        self._clear_btn.connect("clicked", self._on_clear_clicked)
        self._popover_box.append(self._clear_btn)

        self._popover = Gtk.Popover()
        self._popover.add_css_class("genre-filter-popover")
        self._popover.set_child(self._popover_box)
        self._popover.set_parent(self._menu_btn)

    def set_genres(
        self,
        genres: tuple[str, ...],
        enabled_genres: frozenset[str],
    ) -> None:
        self._genres = genres
        self._rebuild_list()
        self.set_enabled_genres(enabled_genres)
        self._update_search_visibility()

    def _update_search_visibility(self) -> None:
        show_search = len(self._genres) >= _SEARCH_MIN_GENRES
        self._search_entry.set_visible(show_search)
        if not show_search:
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

    def _row_height(self) -> int:
        first_row = self._list.get_first_child()
        if first_row is not None:
            _min_h, natural_h, _min_b, _nat_b = first_row.measure(
                Gtk.Orientation.VERTICAL,
                _LIST_WIDTH,
            )
            if natural_h > 0:
                return natural_h
        return _GENRE_ROW_HEIGHT

    def _list_natural_height(self) -> int:
        if not self._genres:
            return 0
        return len(self._genres) * self._row_height()

    def _popover_chrome_height(self) -> int:
        chrome = _POPOVER_BOX_MARGIN_VERTICAL + _POPOVER_CSS_PADDING_VERTICAL
        if self._search_entry.get_visible():
            chrome += self._search_entry.get_margin_bottom() + _SEARCH_ENTRY_HEIGHT
        chrome += self._clear_btn.get_margin_top() + _CLEAR_BTN_HEIGHT
        return chrome

    def _anchor_bounds_in_root(self, root: Gtk.Widget) -> tuple[int, int] | None:
        ok, bounds = self._menu_btn.compute_bounds(root)
        if ok and bounds is not None:
            top = int(bounds.origin.y)
            height = int(bounds.size.height)
            return top, top + max(height, _MENU_BTN_HEIGHT)

        top_coords = self._menu_btn.translate_coordinates(root, 0, 0)
        if top_coords is None:
            return None
        _left, anchor_top_y = top_coords
        top = int(anchor_top_y)
        return top, top + _MENU_BTN_HEIGHT

    def _available_list_height(self, root: Gtk.Widget, anchor_top_y: int, anchor_bottom_y: int) -> int:
        window_height = root.get_height()
        # Popover overlays the grid and now-playing bar; do not reserve bottom chrome.
        space_below = window_height - anchor_bottom_y - _POPOVER_BOTTOM_MARGIN
        space_above = anchor_top_y - _POPOVER_TOP_MARGIN
        directional = max(space_below, space_above) - self._popover_chrome_height()
        window_budget = int(window_height * _WINDOW_HEIGHT_FRACTION) - self._popover_chrome_height()
        return max(directional, window_budget)

    def _compute_list_viewport_height(self) -> int:
        root = self.get_root()
        natural = self._list_natural_height()
        if root is None or not self._menu_btn.get_mapped() or root.get_height() <= 0:
            list_max = _POPOVER_LIST_HEIGHT_FALLBACK
        else:
            bounds = self._anchor_bounds_in_root(root)
            if bounds is None:
                list_max = _POPOVER_LIST_HEIGHT_FALLBACK
            else:
                anchor_top_y, anchor_bottom_y = bounds
                list_max = genre_filter_list_max_height(
                    natural_list_height=natural,
                    available_height=self._available_list_height(
                        root,
                        anchor_top_y,
                        anchor_bottom_y,
                    ),
                )
        if list_max <= 0:
            list_max = _POPOVER_LIST_HEIGHT_FALLBACK
        return list_max

    def _apply_list_viewport_height(self, list_max: int) -> None:
        natural = self._list_natural_height()
        self._scrolled.set_max_content_height(list_max)
        viewport = min(natural, list_max) if natural > 0 else list_max
        self._scrolled.set_size_request(_LIST_WIDTH, viewport)

    def _sync_list_max_height(self) -> None:
        self._apply_list_viewport_height(self._compute_list_viewport_height())

    def _on_check_toggled(self, *_args: object) -> None:
        if self._updating:
            return
        enabled = self.get_enabled_genres()
        self._update_menu_label(enabled)
        if self._on_changed is not None:
            self._on_changed(enabled)

    def _on_menu_btn_clicked(self, *_args: object) -> None:
        self._update_search_visibility()
        self._sync_list_max_height()
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
