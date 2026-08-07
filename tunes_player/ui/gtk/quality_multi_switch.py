"""Quality filter: linked multi-select toggle chips (Compressed / CD / Hi-res)."""

from __future__ import annotations

from collections.abc import Callable

from tunes_player.core.shell_state import (
    QUALITY_FILTER_CD,
    QUALITY_FILTER_COMPRESSED,
    QUALITY_FILTER_HI_RES,
)
from tunes_player.ui.gtk.shell_filter_chips import LinkedMultiSelectChips

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


class QualityMultiSwitch(LinkedMultiSelectChips):
    """Quality heading plus a linked row of on/off toggle buttons (multi-select)."""

    def __init__(
        self,
        *,
        enabled_quality_tiers: frozenset[str],
        on_changed: EnabledQualityTiersChanged | None = None,
    ) -> None:
        super().__init__(
            heading="Quality",
            buckets=_BUCKETS,
            chip_labels=_CHIP_LABELS,
            enabled=enabled_quality_tiers,
            on_changed=on_changed,
            row_css=("quality-filter-row",),
            group_css=("quality-multi-switch",),
        )

    def set_enabled_quality_tiers(self, enabled_quality_tiers: frozenset[str]) -> None:
        self.set_enabled(enabled_quality_tiers)
