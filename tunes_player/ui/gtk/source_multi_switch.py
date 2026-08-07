"""Grouped multi-select source toggles (linked press buttons)."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk  # noqa: E402

from tunes_player.core.models import Source
from tunes_player.ui.gtk.shell_filter_chips import (
    make_chip_toggle,
    make_filter_heading,
    make_linked_chip_group,
)

EnabledSourcesChanged = Callable[[frozenset[Source]], None]

_CHIP_LABELS: dict[Source, str] = {
    Source.LOCAL: "Local",
    Source.TIDAL: "Tidal",
    Source.QOBUZ: "Qobuz",
}


class SourceMultiSwitch(Gtk.Box):
    """Source label plus a linked row of on/off toggle buttons (multi-select)."""

    def __init__(
        self,
        *,
        sources: set[Source],
        enabled_sources: frozenset[Source],
        on_changed: EnabledSourcesChanged | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add_css_class("source-filter-row")
        self.add_css_class("shell-source-multi")
        self.set_valign(Gtk.Align.CENTER)

        self._on_changed = on_changed
        self._updating = False
        self._available: set[Source] = set()
        self._toggles: dict[Source, Gtk.ToggleButton] = {}

        self.append(make_filter_heading("Source"))
        self._group = make_linked_chip_group()
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
            text = _CHIP_LABELS.get(source, source.value.capitalize())
            button = make_chip_toggle(
                text,
                on_toggled=lambda btn, item=source: self._on_toggle_toggled(btn, item),
            )
            self._group.append(button)
            self._toggles[source] = button

        self.set_visible(len(sources) > 1)
        self.set_enabled_sources(enabled_sources)

    def set_enabled_sources(self, enabled_sources: frozenset[Source]) -> None:
        self._updating = True
        try:
            for source, button in self._toggles.items():
                button.set_active(True if not enabled_sources else source in enabled_sources)
        finally:
            self._updating = False

    def get_enabled_sources(self) -> frozenset[Source]:
        """Return enabled subset; empty frozenset means all available sources."""
        active = {source for source, button in self._toggles.items() if button.get_active()}
        if not active or active == self._available:
            return frozenset()
        return frozenset(active)

    def _on_toggle_toggled(self, button: Gtk.ToggleButton, _source: Source) -> None:
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
