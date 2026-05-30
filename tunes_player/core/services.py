"""Facade between UI and backends — expand as features land."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from tunes_player.core.models import Album, Track


@dataclass(frozen=True, slots=True)
class SearchResults:
    albums: list[Album]
    tracks: list[Track]


EventCallback = Callable[[str], None]
Unsubscribe = Callable[[], None]


class PlayerService:
    """Stable API for GTK (and future) frontends."""

    def search(self, query: str) -> SearchResults:
        # Placeholder until local library indexing exists.
        _ = query
        return SearchResults(albums=[], tracks=[])

    def subscribe(self, callback: EventCallback) -> Unsubscribe:
        _ = callback
        return lambda: None
