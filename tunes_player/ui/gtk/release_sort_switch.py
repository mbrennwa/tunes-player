"""Release grid sort: direction flip button plus exclusive criterion chips."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk  # noqa: E402

from tunes_player.core.shell_state import (
    SORT_KEY_ARTIST,
    SORT_KEY_SOURCE,
    SORT_KEY_TITLE,
    SORT_KEY_YEAR,
)

SortStateChanged = Callable[[str | None, bool], None]

_CRITERIA: tuple[str, ...] = (
    SORT_KEY_YEAR,
    SORT_KEY_TITLE,
    SORT_KEY_ARTIST,
    SORT_KEY_SOURCE,
)

_CHIP_LABELS: dict[str, str] = {
    SORT_KEY_YEAR: "Year",
    SORT_KEY_TITLE: "Title",
    SORT_KEY_ARTIST: "Artist",
    SORT_KEY_SOURCE: "Source",
}

_BTN_HEIGHT = 18
_ICON_DESC = "go-down-symbolic"
_ICON_ASC = "go-up-symbolic"


class ReleaseSortSwitch(Gtk.Box):
    """Sort heading, direction button (↑/↓ flip), and one-of-many criterion toggles."""

    def __init__(
        self,
        *,
        sort_key: str | None,
        sort_descending: bool,
        on_changed: SortStateChanged | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add_css_class("release-sort-row")
        self.add_css_class("shell-source-multi")
        self.set_valign(Gtk.Align.CENTER)

        self._on_changed = on_changed
        self._updating = False
        self._sort_key = sort_key
        self._sort_descending = sort_descending
        self._criteria: dict[str, Gtk.ToggleButton] = {}

        heading = Gtk.Label(label="Sort")
        heading.add_css_class("shell-source-heading")
        heading.set_halign(Gtk.Align.START)
        heading.set_valign(Gtk.Align.CENTER)
        self.append(heading)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        controls.set_valign(Gtk.Align.CENTER)

        self._direction_btn = Gtk.Button()
        self._direction_btn.add_css_class("flat")
        self._direction_btn.add_css_class("shell-sort-direction-btn")
        self._direction_btn.set_valign(Gtk.Align.CENTER)
        self._direction_btn.set_margin_top(0)
        self._direction_btn.set_margin_bottom(0)
        self._direction_btn.set_margin_start(0)
        self._direction_btn.set_margin_end(0)
        self._direction_btn.set_size_request(-1, _BTN_HEIGHT)
        self._direction_btn.set_icon_name(_ICON_DESC)
        self._direction_btn.connect("clicked", self._on_direction_clicked)
        controls.append(self._direction_btn)

        self._group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._group.add_css_class("linked")
        self._group.add_css_class("source-multi-switch")
        self._group.add_css_class("release-sort-criteria")
        self._group.set_valign(Gtk.Align.CENTER)
        controls.append(self._group)
        self.append(controls)

        for key in _CRITERIA:
            button = self._make_criterion_toggle(key)
            self._group.append(button)
            self._criteria[key] = button

        self.set_sort_state(sort_key, sort_descending)

    def set_sort_state(self, sort_key: str | None, sort_descending: bool) -> None:
        self._sort_key = sort_key if sort_key in _CRITERIA else None
        self._sort_descending = sort_descending
        self._update_direction_icon()
        self._updating = True
        try:
            for key, button in self._criteria.items():
                button.set_active(key == self._sort_key)
        finally:
            self._updating = False

    def get_sort_key(self) -> str | None:
        return self._sort_key

    def get_sort_descending(self) -> bool:
        return self._sort_descending

    def _make_criterion_toggle(self, key: str) -> Gtk.ToggleButton:
        label = Gtk.Label(label=_CHIP_LABELS.get(key, key))
        label.add_css_class("shell-source-btn-label")

        button = Gtk.ToggleButton()
        button.set_child(label)
        button.add_css_class("flat")
        button.add_css_class("shell-source-btn")
        button.set_valign(Gtk.Align.CENTER)
        button.set_margin_top(0)
        button.set_margin_bottom(0)
        button.set_size_request(-1, _BTN_HEIGHT)
        button.connect("toggled", self._on_criterion_toggled, key)
        return button

    def _update_direction_icon(self) -> None:
        self._direction_btn.set_icon_name(
            _ICON_DESC if self._sort_descending else _ICON_ASC,
        )
        tooltip = self._direction_tooltip()
        if tooltip:
            self._direction_btn.set_tooltip_text(tooltip)
        else:
            self._direction_btn.set_tooltip_text("Toggle sort direction")

    def _direction_tooltip(self) -> str | None:
        if self._sort_key is None:
            return None
        if self._sort_key == SORT_KEY_YEAR:
            return "Newest year first" if self._sort_descending else "Oldest year first"
        if self._sort_key == SORT_KEY_TITLE:
            return "Title A–Z" if self._sort_descending else "Title Z–A"
        if self._sort_key == SORT_KEY_ARTIST:
            return "Artist A–Z" if self._sort_descending else "Artist Z–A"
        if self._sort_key == SORT_KEY_SOURCE:
            return "Source A–Z" if self._sort_descending else "Source Z–A"
        return None

    def _on_direction_clicked(self, *_args: object) -> None:
        self._sort_descending = not self._sort_descending
        self._update_direction_icon()
        self._emit_changed()

    def _on_criterion_toggled(self, button: Gtk.ToggleButton, key: str) -> None:
        if self._updating:
            return

        if button.get_active():
            self._updating = True
            try:
                for other_key, other in self._criteria.items():
                    if other_key != key:
                        other.set_active(False)
            finally:
                self._updating = False
            self._sort_key = key
            self._update_direction_icon()
            self._emit_changed()
            return

        if self._sort_key == key:
            self._sort_key = None
            self._update_direction_icon()
            self._emit_changed()

    def _emit_changed(self) -> None:
        if self._on_changed is not None:
            self._on_changed(self._sort_key, self._sort_descending)
