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
    # Catalog tiers this release is streamable at (for shell quality filter OR match).
    available_quality_tiers: frozenset[str] = frozenset()
    # False for streaming browse stubs until album lookup classifies quality.
    catalog_quality_ready: bool = True
    # Normalized digits-only UPC/EAN from provider album/get; None when absent.
    upc: str | None = None
    # Provider album ids in a UPC merge group; empty when not collapsed.
    edition_release_ids: frozenset[str] = frozenset()
    # Peak album sample rate (Hz) for edition tie-break within a quality bucket.
    peak_sample_rate_hz: int | None = None

    @property
    def has_compressed(self) -> bool:
        return "compressed" in self.available_quality_tiers

    @property
    def has_cd(self) -> bool:
        return "cd" in self.available_quality_tiers

    @property
    def has_hires(self) -> bool:
        return "hi_res" in self.available_quality_tiers

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
