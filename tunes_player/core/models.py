"""Source-agnostic domain models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Source(str, Enum):
    LOCAL = "local"
    TIDAL = "tidal"
    DEEZER = "deezer"
    QOBUZ = "qobuz"


class ReleaseType(str, Enum):
    ALBUM = "album"
    EP = "ep"
    SINGLE = "single"
    COMPILATION = "compilation"
    LIVE_ALBUM = "live_album"
    SYNTHETIC = "synthetic"


class ReleaseCompleteness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    SYNTHETIC = "synthetic"


@dataclass(frozen=True, slots=True)
class Artist:
    id: str
    name: str
    source: Source


@dataclass(frozen=True, slots=True)
class Release:
    id: str
    title: str
    artist_name: str
    source: Source
    track_count: int = 0
    expected_track_count: int | None = None
    completeness: ReleaseCompleteness = ReleaseCompleteness.COMPLETE
    release_type: ReleaseType = ReleaseType.ALBUM
    year: int | None = None
    genre: str | None = None
    art_uri: str | None = None
    duration_sec: float | None = None
    peak_quality_tier: str = ""

    @property
    def is_partial(self) -> bool:
        return self.completeness == ReleaseCompleteness.PARTIAL

    @property
    def is_synthetic(self) -> bool:
        return self.completeness == ReleaseCompleteness.SYNTHETIC


# Backward-compatible alias during migration.
Album = Release


@dataclass(frozen=True, slots=True)
class Track:
    id: str
    title: str
    artist_name: str
    album_title: str | None
    source: Source
    duration_sec: float | None = None
    art_uri: str | None = None
    track_number: int | None = None
    disc_number: int | None = None
