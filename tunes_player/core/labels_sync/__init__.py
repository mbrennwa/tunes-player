"""Cross-machine label sync (issue #80)."""

from __future__ import annotations

from tunes_player.core.labels_sync.format import (
    SYNC_RELATIVE_PATH,
    dumps_label_map,
    is_label_sync_document,
    loads_label_map,
    shard_relative_path,
)
from tunes_player.core.labels_sync.merge import LabelEntry, LabelMap, merge_label_maps
from tunes_player.core.labels_sync.path_hints import (
    looks_like_known_sync_folder,
    unrecognized_sync_folder_advisory,
)
from tunes_player.core.labels_sync.service import LabelSyncService, LabelSyncStatus

__all__ = [
    "SYNC_RELATIVE_PATH",
    "LabelEntry",
    "LabelMap",
    "LabelSyncService",
    "LabelSyncStatus",
    "dumps_label_map",
    "is_label_sync_document",
    "loads_label_map",
    "looks_like_known_sync_folder",
    "merge_label_maps",
    "shard_relative_path",
    "unrecognized_sync_folder_advisory",
]
