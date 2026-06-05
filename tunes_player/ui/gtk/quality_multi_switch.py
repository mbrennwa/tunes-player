"""Quality filter: linked multi-select toggle chips (Compressed / CD / Hi-res)."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk  # noqa: E402

from tunes_player.core.shell_state import (
    QUALITY_FILTER_CD,
    QUALITY_FILTER_COMPRESSED,
    QUALITY_FILTER_HI_RES,
)

EnabledQualityTiersChanged = Callable[[frozenset[str]], None]

_BUCKETS: tuple[str, ...] = (
    QUALITY_FILTER_COMPRESSED,
    QUALITY_FILTER_CD,
    QUALITY_FILTER_HI_RES,
)

_CHIP_LABELS: dict[str, str] = {
    QUALITY_FILTER_COMPRESSED: "Compressed",
    QUALITY_FILTER_CD: "CD",
    QUALITY_FILTER_HI_RES: "Hi-res",
}

_BTN_HEIGHT = 18


class QualityMultiSwitch(Gtk.Box):
    """Quality heading plus a linked row of on/off toggle buttons (multi-select)."""

    def __init__(
        self,
        *,
        enabled_quality_tiers: frozenset[str],
        on_changed: EnabledQualityTiersChanged | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add_css_class("quality-filter-row")
        self.add_css_class("shell-source-multi")
        self.set_valign(Gtk.Align.CENTER)

        self._on_changed = on_changed
        self._updating = False
        self._toggles: dict[str, Gtk.ToggleButton] = {}

        heading = Gtk.Label(label="Quality")
        heading.add_css_class("shell-source-heading")
        heading.set_halign(Gtk.Align.START)
        heading.set_valign(Gtk.Align.CENTER)
        self.append(heading)

        self._group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._group.add_css_class("linked")
        self._group.add_css_class("source-multi-switch")
        self._group.add_css_class("quality-multi-switch")
        self._group.set_valign(Gtk.Align.CENTER)
        self.append(self._group)

        for bucket in _BUCKETS:
            button = self._make_toggle(bucket)
            self._group.append(button)
            self._toggles[bucket] = button

        self.set_enabled_quality_tiers(enabled_quality_tiers)

    def _make_toggle(self, bucket: str) -> Gtk.ToggleButton:
        text = _CHIP_LABELS.get(bucket, bucket)
        label = Gtk.Label(label=text)
        label.add_css_class("shell-source-btn-label")

        button = Gtk.ToggleButton()
        button.set_child(label)
        button.add_css_class("flat")
        button.add_css_class("shell-source-btn")
        button.set_valign(Gtk.Align.CENTER)
        button.set_margin_top(0)
        button.set_margin_bottom(0)
        button.set_size_request(-1, _BTN_HEIGHT)
        button.connect("toggled", self._on_toggle_toggled, bucket)
        return button

    def _toggle_active(self, bucket: str, enabled: frozenset[str]) -> bool:
        if not enabled:
            return True
        return bucket in enabled

    def set_enabled_quality_tiers(self, enabled_quality_tiers: frozenset[str]) -> None:
        self._updating = True
        try:
            for bucket, button in self._toggles.items():
                button.set_active(self._toggle_active(bucket, enabled_quality_tiers))
        finally:
            self._updating = False

    def get_enabled_quality_tiers(self) -> frozenset[str]:
        """Return enabled subset; empty frozenset means all buckets."""
        active = {
            bucket for bucket, toggle in self._toggles.items() if toggle.get_active()
        }
        if not active or active == frozenset(_BUCKETS):
            return frozenset()
        return frozenset(active)

    def _on_toggle_toggled(self, button: Gtk.ToggleButton, bucket: str) -> None:
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
            self._on_changed(self.get_enabled_quality_tiers())
