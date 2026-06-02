"""Home feed item types (Recently added, etc.)."""

from __future__ import annotations

from dataclasses import dataclass

from tunes_player.core.models import Release


@dataclass(frozen=True, slots=True)
class RecentlyAddedItem:
    added_ns: int
    release: Release
