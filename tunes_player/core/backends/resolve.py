"""Dispatch track IDs to the correct backend."""

from __future__ import annotations

from tunes_player.core.backends.local import resolve_local_track
from tunes_player.core.backends.playable import PlayableSource
from tunes_player.core.backends.qobuz.client import QobuzClient, QobuzUnavailableError
from tunes_player.core.backends.tidal.client import TidalClient, TidalUnavailableError
from tunes_player.core.library.store import LibraryStore
from tunes_player.core.release_quality import PlaybackQualityPolicy


def resolve_track(
    store: LibraryStore,
    track_id: str,
    *,
    tidal: TidalClient | None,
    qobuz: QobuzClient | None = None,
    playback_quality_policy: PlaybackQualityPolicy | None = None,
) -> PlayableSource | None:
    if track_id.startswith("tidal:"):
        if tidal is None:
            return None
        try:
            return tidal.resolve_playable(
                track_id,
                playback_quality_policy=playback_quality_policy,
            )
        except TidalUnavailableError:
            raise
    if track_id.startswith("qobuz:"):
        if qobuz is None:
            return None
        try:
            return qobuz.resolve_playable(
                track_id,
                playback_quality_policy=playback_quality_policy,
            )
        except QobuzUnavailableError:
            raise
    if track_id.startswith("local:"):
        return resolve_local_track(store, track_id)
    return resolve_local_track(store, track_id)
