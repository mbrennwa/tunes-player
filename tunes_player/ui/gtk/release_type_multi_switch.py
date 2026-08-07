"""Release-type filter: linked multi-select toggle chips (Album / Singles / EP / Other)."""

from __future__ import annotations

from collections.abc import Callable

from tunes_player.core.shell_state import (
    RELEASE_TYPE_FILTER_ALBUM,
    RELEASE_TYPE_FILTER_EP,
    RELEASE_TYPE_FILTER_OTHER,
    RELEASE_TYPE_FILTER_SINGLE,
)
from tunes_player.ui.gtk.shell_filter_chips import LinkedMultiSelectChips

EnabledReleaseTypesChanged = Callable[[frozenset[str]], None]

_BUCKETS: tuple[str, ...] = (
    RELEASE_TYPE_FILTER_ALBUM,
    RELEASE_TYPE_FILTER_SINGLE,
    RELEASE_TYPE_FILTER_EP,
    RELEASE_TYPE_FILTER_OTHER,
)

_CHIP_LABELS: dict[str, str] = {
    RELEASE_TYPE_FILTER_ALBUM: "Album",
    RELEASE_TYPE_FILTER_SINGLE: "Singles",
    RELEASE_TYPE_FILTER_EP: "EP",
    RELEASE_TYPE_FILTER_OTHER: "Other",
}


class ReleaseTypeMultiSwitch(LinkedMultiSelectChips):
    """Type heading plus a linked row of on/off toggle buttons (multi-select)."""

    def __init__(
        self,
        *,
        enabled_release_types: frozenset[str],
        on_changed: EnabledReleaseTypesChanged | None = None,
    ) -> None:
        super().__init__(
            heading="Type",
            buckets=_BUCKETS,
            chip_labels=_CHIP_LABELS,
            enabled=enabled_release_types,
            on_changed=on_changed,
            row_css=("release-type-filter-row",),
            group_css=("release-type-multi-switch",),
        )

    def set_enabled_release_types(self, enabled_release_types: frozenset[str]) -> None:
        self.set_enabled(enabled_release_types)
