"""Cross-machine label sync (issue #80)."""

from __future__ import annotations

from tunes_player.core.labels_sync.format import (
    SYNC_RELATIVE_PATH,
    dumps_label_map,
    loads_label_map,
)
from tunes_player.core.labels_sync.merge import LabelEntry, LabelMap, merge_label_maps
from tunes_player.core.labels_sync.service import LabelSyncService, LabelSyncStatus

__all__ = [
    "SYNC_RELATIVE_PATH",
    "LabelEntry",
    "LabelMap",
    "LabelSyncService",
    "LabelSyncStatus",
    "dumps_label_map",
    "loads_label_map",
    "merge_label_maps",
]
