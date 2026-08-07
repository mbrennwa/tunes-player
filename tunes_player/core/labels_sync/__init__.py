"""Cross-machine label sync (issue #80)."""

from __future__ import annotations

from tunes_player.core.labels_sync.path_hints import (
    looks_like_known_sync_folder,
    unrecognized_sync_folder_advisory,
)
from tunes_player.core.labels_sync.service import LabelSyncService, LabelSyncStatus

__all__ = [
    "LabelSyncService",
    "LabelSyncStatus",
    "looks_like_known_sync_folder",
    "unrecognized_sync_folder_advisory",
]
