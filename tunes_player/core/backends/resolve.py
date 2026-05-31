"""Dispatch track IDs to the correct backend."""

from __future__ import annotations

from tunes_player.core.backends.local import resolve_local_track
from tunes_player.core.backends.playable import PlayableSource
from tunes_player.core.backends.tidal.client import TidalClient, TidalUnavailableError
from tunes_player.core.library.store import LibraryStore


def resolve_track(
    store: LibraryStore,
    track_id: str,
    *,
    tidal: TidalClient | None,
) -> PlayableSource | None:
    if track_id.startswith("tidal:"):
        if tidal is None:
            return None
        try:
            return tidal.resolve_playable(track_id)
        except TidalUnavailableError:
            raise
    if track_id.startswith("local:"):
        return resolve_local_track(store, track_id)
    return resolve_local_track(store, track_id)
