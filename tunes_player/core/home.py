"""Home feed item types (Recently added, etc.)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tunes_player.core.models import Album, Track

RecentlyAddedKind = Literal["album", "track"]


@dataclass(frozen=True, slots=True)
class RecentlyAddedItem:
    kind: RecentlyAddedKind
    added_ns: int
    album: Album | None = None
    track: Track | None = None
