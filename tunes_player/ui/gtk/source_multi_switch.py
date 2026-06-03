"""Grouped multi-select source toggles (linked press buttons)."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk  # noqa: E402

from tunes_player.core.models import Source
from tunes_player.ui.gtk.util import source_label

EnabledSourcesChanged = Callable[[frozenset[Source]], None]


class SourceMultiSwitch(Gtk.Box):
    """Source label plus a linked row of on/off toggle buttons (multi-select)."""

    def __init__(
        self,
        *,
        sources: set[Source],
        enabled_sources: frozenset[Source],
        on_changed: EnabledSourcesChanged | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.add_css_class("source-filter-row")
        self.set_valign(Gtk.Align.CENTER)

        self._on_changed = on_changed
        self._updating = False
        self._available: set[Source] = set()
        self._toggles: dict[Source, Gtk.ToggleButton] = {}

        heading = Gtk.Label(label="Source")
        heading.add_css_class("dim-label")
        heading.set_halign(Gtk.Align.START)
        heading.set_valign(Gtk.Align.CENTER)
        self.append(heading)

        self._group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._group.add_css_class("linked")
        self._group.add_css_class("source-multi-switch")
        self._group.set_valign(Gtk.Align.CENTER)
        self.append(self._group)

        self.set_sources(sources, enabled_sources)

    def set_sources(
        self,
        sources: set[Source],
        enabled_sources: frozenset[Source],
    ) -> None:
        self._available = set(sources)
        child = self._group.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._group.remove(child)
            child = next_child
        self._toggles.clear()

        for source in sorted(sources, key=lambda item: item.value):
            button = Gtk.ToggleButton(label=source_label(source))
            button.add_css_class("flat")
            button.set_valign(Gtk.Align.CENTER)
            button.connect("toggled", self._on_toggle_toggled, source)
            self._group.append(button)
            self._toggles[source] = button

        self.set_visible(len(sources) > 1)
        self.set_enabled_sources(enabled_sources)

    def _toggle_active(self, source: Source, enabled_sources: frozenset[Source]) -> bool:
        if not enabled_sources:
            return True
        return source in enabled_sources

    def set_enabled_sources(self, enabled_sources: frozenset[Source]) -> None:
        self._updating = True
        try:
            for source, button in self._toggles.items():
                button.set_active(self._toggle_active(source, enabled_sources))
        finally:
            self._updating = False

    def get_enabled_sources(self) -> frozenset[Source]:
        """Return enabled subset; empty frozenset means all available sources."""
        active = {source for source, button in self._toggles.items() if button.get_active()}
        if not active or active == self._available:
            return frozenset()
        return frozenset(active)

    def _on_toggle_toggled(self, button: Gtk.ToggleButton, source: Source) -> None:
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
            self._on_changed(self.get_enabled_sources())
