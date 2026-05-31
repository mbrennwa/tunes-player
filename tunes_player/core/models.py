"""Source-agnostic domain models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Source(str, Enum):
    LOCAL = "local"
    TIDAL = "tidal"
    DEEZER = "deezer"
    QOBUZ = "qobuz"


@dataclass(frozen=True, slots=True)
class Artist:
    id: str
    name: str
    source: Source


@dataclass(frozen=True, slots=True)
class Album:
    id: str
    title: str
    artist_name: str
    source: Source
    year: int | None = None
    track_count: int = 0
    art_uri: str | None = None


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
