"""Home feed item types (Recently added, etc.)."""

from __future__ import annotations

from dataclasses import dataclass

from tunes_player.core.models import Release, Source


@dataclass(frozen=True, slots=True)
class RecentlyAddedItem:
    added_ns: int
    release: Release


# New Releases view limits (local index window + per-source fetch, then merged cap).
NEW_MUSIC_MERGE_LIMIT = 300
NEW_MUSIC_LOCAL_WITHIN_DAYS_DEFAULT = 90
NEW_MUSIC_LOCAL_WITHIN_DAYS_MIN = 1
NEW_MUSIC_LOCAL_WITHIN_DAYS_MAX = 365
NEW_MUSIC_LOCAL_LIMIT = 250
NEW_MUSIC_STREAMING_PER_SOURCE_LIMIT = 300

# Suggestions view limits (merged flat grid; added_ns is sort/rank key).
SUGGESTIONS_MERGE_LIMIT = 300
SUGGESTIONS_STREAMING_PER_SOURCE_LIMIT = 300
SUGGESTIONS_LOCAL_CONTINUE_LIMIT = 40
SUGGESTIONS_LOCAL_REDISCOVER_LIMIT = 80
SUGGESTIONS_REDISCOVER_IDLE_MONTHS = 18
SUGGESTIONS_SIMILAR_LIMIT = 24
SUGGESTIONS_RECENT_GENRE_DAYS = 30

# Merged suggestions sort: local first, then streaming by source name (Deezer, Qobuz, Tidal).
_SUGGESTION_SUB_SCALE = 10**15
_SUGGESTION_LOCAL_BASE = 3 * 10**18
_SUGGESTION_SOURCE_STEP = 10**16
# Higher rank sorts earlier among streaming (alphabetical: deezer > qobuz > tidal).
_STREAMING_SOURCE_RANK: dict[Source, int] = {
    Source.DEEZER: 2,
    Source.QOBUZ: 1,
    Source.TIDAL: 0,
}


def suggestion_added_ns(
    source: Source,
    *,
    played_at_ns: int | None = None,
    index: int = 0,
) -> int:
    """Build a sort key (higher = earlier in the grid).

    Local releases first; then streaming providers in name order (Deezer, Qobuz, TIDAL).
    Within each group, newer plays or lower index rank higher.
    """
    if played_at_ns is not None:
        sub = min(max(played_at_ns, 0), _SUGGESTION_SUB_SCALE - 1)
    else:
        sub = max(_SUGGESTION_SUB_SCALE - 1 - index, 0)
    if source == Source.LOCAL:
        return _SUGGESTION_LOCAL_BASE + sub
    rank = _STREAMING_SOURCE_RANK.get(source, 0)
    return rank * _SUGGESTION_SOURCE_STEP + sub
