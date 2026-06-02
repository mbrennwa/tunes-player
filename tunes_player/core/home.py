"""Home feed item types (Recently added, etc.)."""

from __future__ import annotations

from dataclasses import dataclass

from tunes_player.core.models import Release


@dataclass(frozen=True, slots=True)
class RecentlyAddedItem:
    added_ns: int
    release: Release


# New Music view limits (local index window + per-source fetch, then merged cap).
NEW_MUSIC_MERGE_LIMIT = 300
NEW_MUSIC_LOCAL_WITHIN_DAYS_DEFAULT = 90
NEW_MUSIC_LOCAL_WITHIN_DAYS_MIN = 1
NEW_MUSIC_LOCAL_WITHIN_DAYS_MAX = 365
NEW_MUSIC_LOCAL_LIMIT = 250
NEW_MUSIC_STREAMING_PER_SOURCE_LIMIT = 300
