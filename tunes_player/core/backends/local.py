"""Local file backend — Track ID to file:// URI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tunes_player.core.library.store import LibraryStore
from tunes_player.core.models import Track


@dataclass(frozen=True, slots=True)
class PlayableSource:
    uri: str
    path: str
    metadata: Track
    start_sec: float = 0


def resolve_local_track(store: LibraryStore, track_id: str) -> PlayableSource | None:
    track = store.get_track(track_id)
    if track is None:
        return None
    metadata = store.get_file_metadata(track_id)
    if metadata is None:
        return None
    path = Path(metadata.path)
    if not path.is_file():
        return None
    resolved = path.resolve()
    return PlayableSource(
        uri=resolved.as_uri(),
        path=str(resolved),
        metadata=track,
    )
