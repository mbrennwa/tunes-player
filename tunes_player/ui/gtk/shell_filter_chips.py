"""Shared shell filter chip helpers (linked multi-select rows)."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping, Sequence
from typing import TypeVar

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk  # noqa: E402

K = TypeVar("K", bound=Hashable)

CHIP_BTN_HEIGHT = 18

EnabledKeysChanged = Callable[[frozenset[K]], None]


def make_filter_heading(text: str) -> Gtk.Label:
    heading = Gtk.Label(label=text)
    heading.add_css_class("shell-source-heading")
    heading.set_halign(Gtk.Align.START)
    heading.set_valign(Gtk.Align.CENTER)
    return heading


def make_chip_toggle(
    text: str,
    *,
    on_toggled: Callable[[Gtk.ToggleButton], None] | None = None,
    height: int = CHIP_BTN_HEIGHT,
) -> Gtk.ToggleButton:
    label = Gtk.Label(label=text)
    label.add_css_class("shell-source-btn-label")

    button = Gtk.ToggleButton()
    button.set_child(label)
    button.add_css_class("flat")
    button.add_css_class("shell-source-btn")
    button.set_valign(Gtk.Align.CENTER)
    button.set_margin_top(0)
    button.set_margin_bottom(0)
    button.set_size_request(-1, height)
    if on_toggled is not None:
        button.connect("toggled", on_toggled)
    return button


def make_linked_chip_group(*extra_css: str) -> Gtk.Box:
    group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    group.add_css_class("linked")
    group.add_css_class("source-multi-switch")
    for css in extra_css:
        group.add_css_class(css)
    group.set_valign(Gtk.Align.CENTER)
    return group


class LinkedMultiSelectChips(Gtk.Box):
    """Heading plus linked on/off chips; empty selection means “all”."""

    def __init__(
        self,
        *,
        heading: str,
        buckets: Sequence[K],
        chip_labels: Mapping[K, str],
        enabled: frozenset[K],
        on_changed: EnabledKeysChanged[K] | None = None,
        row_css: Sequence[str] = (),
        group_css: Sequence[str] = (),
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add_css_class("shell-source-multi")
        for css in row_css:
            self.add_css_class(css)
        self.set_valign(Gtk.Align.CENTER)

        self._on_changed = on_changed
        self._updating = False
        self._buckets: tuple[K, ...] = tuple(buckets)
        self._chip_labels = dict(chip_labels)
        self._toggles: dict[K, Gtk.ToggleButton] = {}

        self.append(make_filter_heading(heading))
        self._group = make_linked_chip_group(*group_css)
        self.append(self._group)

        for key in self._buckets:
            text = self._chip_labels.get(key, str(key))
            button = make_chip_toggle(
                text,
                on_toggled=lambda btn, item=key: self._on_toggle_toggled(btn, item),
            )
            self._group.append(button)
            self._toggles[key] = button

        self.set_enabled(enabled)

    def set_enabled(self, enabled: frozenset[K]) -> None:
        self._updating = True
        try:
            for key, button in self._toggles.items():
                button.set_active(True if not enabled else key in enabled)
        finally:
            self._updating = False

    def get_enabled(self) -> frozenset[K]:
        """Return enabled subset; empty frozenset means all buckets."""
        active = {key for key, toggle in self._toggles.items() if toggle.get_active()}
        if not active or active == frozenset(self._buckets):
            return frozenset()
        return frozenset(active)

    def _on_toggle_toggled(self, button: Gtk.ToggleButton, _key: K) -> None:
        if self._updating:
            return
        active = {item for item, toggle in self._toggles.items() if toggle.get_active()}
        if not active:
            self._updating = True
            try:
                button.set_active(True)
            finally:
                self._updating = False
            return
        if self._on_changed is not None:
            self._on_changed(self.get_enabled())
