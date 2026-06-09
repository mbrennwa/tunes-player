"""Shell UI state: base selection, search query, and source filter."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from tunes_player.core.home import RecentlyAddedItem
from tunes_player.core.models import (
    Release,
    ReleaseCompleteness,
    ReleaseType,
    Source,
)
from tunes_player.core import release_quality as _release_quality
from tunes_player.core.release_quality import (
    release_available_quality_tiers,
    release_matches_quality_filter,
    release_quality_filter_bucket,
)

QUALITY_FILTER_COMPRESSED = _release_quality.QUALITY_FILTER_COMPRESSED
QUALITY_FILTER_CD = _release_quality.QUALITY_FILTER_CD
QUALITY_FILTER_HI_RES = _release_quality.QUALITY_FILTER_HI_RES


class ShellBase(str, Enum):
    NONE = "none"
    SEARCH = "search"
    NEW_MUSIC = "new_music"
    SUGGESTION = "suggestion"
    ALL_LOCAL = "all_local"


class SearchScope(str, Enum):
    ALL = "all"
    ARTIST = "artist"


_VALID_BASES = frozenset(item.value for item in ShellBase)
_VALID_SEARCH_SCOPES = frozenset(item.value for item in SearchScope)
_VALID_SOURCES = frozenset(item.value for item in Source)
_VALID_COMPLETENESS = frozenset(item.value for item in ReleaseCompleteness)
_VALID_RELEASE_TYPES = frozenset(item.value for item in ReleaseType)

NO_GENRE_LABEL = "(No genre)"

RELEASE_TYPE_FILTER_ALBUM = "album"
RELEASE_TYPE_FILTER_SINGLE = "single"
RELEASE_TYPE_FILTER_EP = "ep"
RELEASE_TYPE_FILTER_OTHER = "other"
_VALID_RELEASE_TYPE_FILTERS = frozenset(
    {
        RELEASE_TYPE_FILTER_ALBUM,
        RELEASE_TYPE_FILTER_SINGLE,
        RELEASE_TYPE_FILTER_EP,
        RELEASE_TYPE_FILTER_OTHER,
    }
)

_VALID_QUALITY_FILTERS = frozenset(
    {
        QUALITY_FILTER_COMPRESSED,
        QUALITY_FILTER_CD,
        QUALITY_FILTER_HI_RES,
    }
)

SORT_KEY_YEAR = "year"
SORT_KEY_ALBUM = "album"
SORT_KEY_ARTIST = "artist"
SORT_KEY_SOURCE = "source"
_VALID_SORT_KEYS = frozenset(
    {
        SORT_KEY_YEAR,
        SORT_KEY_ALBUM,
        SORT_KEY_ARTIST,
        SORT_KEY_SOURCE,
    }
)


@dataclass(frozen=True, slots=True)
class ShellState:
    base: ShellBase = ShellBase.NONE
    search_query: str = ""
    search_scope: SearchScope = SearchScope.ALL
    # Empty set = all configured sources enabled. Non-empty = only those sources.
    enabled_sources: frozenset[Source] = field(default_factory=frozenset)
    # Empty set = no genre filter. Non-empty = OR match on release genre buckets.
    enabled_genres: frozenset[str] = field(default_factory=frozenset)
    # Empty set = all release-type buckets enabled. Non-empty = OR on filter buckets.
    enabled_release_types: frozenset[str] = field(default_factory=frozenset)
    # Empty set = all quality tiers enabled. Non-empty = OR on available_quality_tiers.
    enabled_quality_tiers: frozenset[str] = field(default_factory=frozenset)
    # None = preserve cache order; otherwise single-field sort after filters.
    sort_key: str | None = None
    sort_descending: bool = True
    # Serialized grid rows for instant restore on relaunch (no API refetch).
    cached_releases: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "base": self.base.value,
            "search_query": self.search_query,
        }
        if self.base == ShellBase.SEARCH and self.search_scope != SearchScope.ALL:
            payload["search_scope"] = self.search_scope.value
        if self.enabled_sources:
            payload["enabled_sources"] = sorted(
                source.value for source in self.enabled_sources
            )
        if self.enabled_genres:
            payload["enabled_genres"] = sorted(self.enabled_genres)
        if self.enabled_release_types:
            payload["enabled_release_types"] = sorted(self.enabled_release_types)
        if self.enabled_quality_tiers:
            payload["enabled_quality_tiers"] = sorted(self.enabled_quality_tiers)
        if self.sort_key is not None:
            payload["sort_key"] = self.sort_key
        if self.sort_descending is not True:
            payload["sort_descending"] = self.sort_descending
        if self.cached_releases:
            payload["cached_releases"] = list(self.cached_releases)
        return payload

    @classmethod
    def from_dict(cls, raw: object) -> ShellState:
        if not isinstance(raw, dict):
            return cls()
        base_raw = raw.get("base", ShellBase.NONE.value)
        base = ShellBase.NONE
        if isinstance(base_raw, str) and base_raw in _VALID_BASES:
            base = ShellBase(base_raw)
        query = raw.get("search_query", "")
        search_query = query.strip() if isinstance(query, str) else ""
        search_scope = SearchScope.ALL
        if base != ShellBase.SEARCH:
            search_query = ""
        else:
            scope_raw = raw.get("search_scope")
            if isinstance(scope_raw, str) and scope_raw in _VALID_SEARCH_SCOPES:
                search_scope = SearchScope(scope_raw)
        enabled_sources = _parse_enabled_sources(raw)
        enabled_genres = _parse_enabled_genres(raw)
        enabled_release_types = _parse_enabled_release_types(raw)
        enabled_quality_tiers = _parse_enabled_quality_tiers(raw)
        sort_key, sort_descending = _parse_sort_state(raw)
        cached_releases = _parse_cached_releases(raw)
        return cls(
            base=base,
            search_query=search_query,
            search_scope=search_scope,
            enabled_sources=enabled_sources,
            enabled_genres=enabled_genres,
            enabled_release_types=enabled_release_types,
            enabled_quality_tiers=enabled_quality_tiers,
            sort_key=sort_key,
            sort_descending=sort_descending,
            cached_releases=cached_releases,
        )


def release_to_cache_payload(release: Release) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": release.id,
        "title": release.title,
        "artist_name": release.artist_name,
        "source": release.source.value,
        "track_count": release.track_count,
        "completeness": release.completeness.value,
        "release_type": release.release_type.value,
    }
    if release.expected_track_count is not None:
        payload["expected_track_count"] = release.expected_track_count
    if release.year is not None:
        payload["year"] = release.year
    if release.genre:
        payload["genre"] = release.genre
    if release.art_uri:
        payload["art_uri"] = release.art_uri
    if release.duration_sec is not None:
        payload["duration_sec"] = release.duration_sec
    payload["peak_quality_tier"] = (
        release.peak_quality_tier
        if release.peak_quality_tier in _VALID_QUALITY_FILTERS
        else QUALITY_FILTER_COMPRESSED
    )
    available = release_available_quality_tiers(release)
    if available:
        payload["available_quality_tiers"] = sorted(available)
    return payload


def release_from_cache_payload(raw: object) -> Release | None:
    if not isinstance(raw, dict):
        return None
    release_id = raw.get("id")
    title = raw.get("title")
    artist_name = raw.get("artist_name")
    source_raw = raw.get("source")
    if not isinstance(release_id, str) or not release_id:
        return None
    if not isinstance(title, str) or not isinstance(artist_name, str):
        return None
    if not isinstance(source_raw, str) or source_raw not in _VALID_SOURCES:
        return None
    completeness_raw = raw.get("completeness", ReleaseCompleteness.COMPLETE.value)
    release_type_raw = raw.get("release_type", ReleaseType.ALBUM.value)
    completeness = ReleaseCompleteness.COMPLETE
    if isinstance(completeness_raw, str) and completeness_raw in _VALID_COMPLETENESS:
        completeness = ReleaseCompleteness(completeness_raw)
    release_type = ReleaseType.ALBUM
    if isinstance(release_type_raw, str) and release_type_raw in _VALID_RELEASE_TYPES:
        release_type = ReleaseType(release_type_raw)
    expected = raw.get("expected_track_count")
    year = raw.get("year")
    genre = raw.get("genre")
    art_uri = raw.get("art_uri")
    duration = raw.get("duration_sec")
    try:
        track_count = int(raw.get("track_count", 0))
    except (TypeError, ValueError):
        track_count = 0
    peak_quality_tier_raw = raw.get("peak_quality_tier", QUALITY_FILTER_COMPRESSED)
    peak_quality_tier = QUALITY_FILTER_COMPRESSED
    if (
        isinstance(peak_quality_tier_raw, str)
        and peak_quality_tier_raw in _VALID_QUALITY_FILTERS
    ):
        peak_quality_tier = peak_quality_tier_raw
    available_quality_tiers: frozenset[str] = frozenset()
    available_raw = raw.get("available_quality_tiers")
    if isinstance(available_raw, list):
        parsed = frozenset(
            str(item)
            for item in available_raw
            if isinstance(item, str) and item in _VALID_QUALITY_FILTERS
        )
        if parsed:
            available_quality_tiers = parsed
    return Release(
        id=release_id,
        title=title,
        artist_name=artist_name,
        source=Source(source_raw),
        track_count=track_count,
        expected_track_count=int(expected) if expected is not None else None,
        completeness=completeness,
        release_type=release_type,
        year=int(year) if year is not None else None,
        genre=str(genre) if genre else None,
        art_uri=str(art_uri) if art_uri else None,
        duration_sec=float(duration) if duration is not None else None,
        peak_quality_tier=peak_quality_tier,
        available_quality_tiers=available_quality_tiers,
    )


def refresh_local_peak_quality_tiers(
    releases: list[Release],
    *,
    local_tier_by_id: dict[str, str],
    local_available_tiers_by_id: dict[str, frozenset[str]] | None = None,
) -> list[Release]:
    """Replace stale cached quality tier values for local releases."""
    if not local_tier_by_id and not local_available_tiers_by_id:
        return list(releases)
    refreshed: list[Release] = []
    for release in releases:
        if release.source != Source.LOCAL:
            refreshed.append(release)
            continue
        tier = local_tier_by_id.get(release.id)
        available = (
            local_available_tiers_by_id.get(release.id)
            if local_available_tiers_by_id is not None
            else None
        )
        if tier and tier != release.peak_quality_tier:
            kwargs: dict[str, object] = {"peak_quality_tier": tier}
            if available is not None:
                kwargs["available_quality_tiers"] = available
            refreshed.append(replace(release, **kwargs))
        elif available is not None and available != release.available_quality_tiers:
            refreshed.append(
                replace(release, available_quality_tiers=available),
            )
        else:
            refreshed.append(release)
    return refreshed


def cached_releases_have_quality_tiers(
    payloads: tuple[dict[str, Any], ...] | list[dict[str, Any]],
) -> bool:
    """True when every cached row includes a valid peak_quality_tier."""
    if not payloads:
        return False
    for item in payloads:
        if not isinstance(item, dict):
            return False
        tier = item.get("peak_quality_tier")
        if not isinstance(tier, str) or tier not in _VALID_QUALITY_FILTERS:
            return False
    return True


def releases_from_cache_payloads(
    payloads: tuple[dict[str, Any], ...],
) -> list[Release]:
    releases: list[Release] = []
    for item in payloads:
        release = release_from_cache_payload(item)
        if release is not None:
            releases.append(release)
    return releases


def _parse_cached_releases(raw: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    cached_raw = raw.get("cached_releases")
    if isinstance(cached_raw, list):
        payloads: list[dict[str, Any]] = []
        for item in cached_raw:
            if isinstance(item, dict):
                payloads.append(dict(item))
        if payloads:
            return tuple(payloads)
    return ()


def _parse_sort_state(raw: dict[str, Any]) -> tuple[str | None, bool]:
    sort_key_raw = raw.get("sort_key")
    sort_key = None
    if isinstance(sort_key_raw, str) and sort_key_raw in _VALID_SORT_KEYS:
        sort_key = sort_key_raw
    sort_descending = raw.get("sort_descending", True)
    if isinstance(sort_descending, bool):
        return sort_key, sort_descending
    return sort_key, True


def _parse_enabled_release_types(raw: dict[str, Any]) -> frozenset[str]:
    enabled_raw = raw.get("enabled_release_types")
    if not isinstance(enabled_raw, list):
        return frozenset()
    return frozenset(
        str(item)
        for item in enabled_raw
        if isinstance(item, str) and item in _VALID_RELEASE_TYPE_FILTERS
    )


def _parse_enabled_quality_tiers(raw: dict[str, Any]) -> frozenset[str]:
    enabled_raw = raw.get("enabled_quality_tiers")
    if not isinstance(enabled_raw, list):
        return frozenset()
    return frozenset(
        str(item)
        for item in enabled_raw
        if isinstance(item, str) and item in _VALID_QUALITY_FILTERS
    )


def _parse_enabled_genres(raw: dict[str, Any]) -> frozenset[str]:
    enabled_raw = raw.get("enabled_genres")
    if not isinstance(enabled_raw, list):
        return frozenset()
    parsed = frozenset(
        str(item).strip()
        for item in enabled_raw
        if isinstance(item, str) and str(item).strip()
    )
    return parsed


def _parse_enabled_sources(raw: dict[str, Any]) -> frozenset[Source]:
    enabled_raw = raw.get("enabled_sources")
    if isinstance(enabled_raw, list):
        parsed = frozenset(
            Source(str(item))
            for item in enabled_raw
            if isinstance(item, str) and item in _VALID_SOURCES
        )
        if parsed:
            return parsed
    filter_raw = raw.get("source_filter")
    if isinstance(filter_raw, str) and filter_raw in _VALID_SOURCES:
        return frozenset({Source(filter_raw)})
    return frozenset()


def parse_shell_state(raw: object) -> ShellState:
    return ShellState.from_dict(raw)


def ensure_source_enabled(
    enabled_sources: frozenset[Source],
    source: Source,
    *,
    available: set[Source] | frozenset[Source],
) -> frozenset[Source]:
    """Ensure *source* is enabled without changing other explicit source toggles."""
    if source not in available:
        return enabled_sources
    if not enabled_sources or source in enabled_sources:
        return enabled_sources
    return enabled_sources | frozenset({source})


def prune_enabled_sources(
    enabled_sources: frozenset[Source],
    available_sources: set[Source] | frozenset[Source],
) -> frozenset[Source]:
    available = frozenset(available_sources)
    return frozenset(source for source in enabled_sources if source in available)


def filter_releases_to_available_sources(
    releases: list[Release],
    available_sources: frozenset[Source],
) -> list[Release]:
    if not available_sources:
        return []
    return [release for release in releases if release.source in available_sources]


def cached_releases_compatible_with_available(
    payloads: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    available_sources: frozenset[Source],
) -> bool:
    """True when at least one cached row is from a currently available source."""
    if not payloads or not available_sources:
        return False
    for item in payloads:
        if not isinstance(item, dict):
            continue
        source_raw = item.get("source")
        if isinstance(source_raw, str) and source_raw in _VALID_SOURCES:
            if Source(source_raw) in available_sources:
                return True
    return False


def apply_source_filter(
    releases: list[Release],
    enabled_sources: frozenset[Source],
    *,
    available_sources: frozenset[Source] | None = None,
) -> list[Release]:
    if available_sources is not None:
        releases = filter_releases_to_available_sources(releases, available_sources)
    if not enabled_sources:
        return list(releases)
    return [release for release in releases if release.source in enabled_sources]


def release_genre_bucket(
    release: Release,
    *,
    no_genre_label: str = NO_GENRE_LABEL,
) -> str:
    genre = (release.genre or "").strip()
    return genre if genre else no_genre_label


def genres_in_selection(
    releases: list[Release] | tuple[Release, ...],
    *,
    no_genre_label: str = NO_GENRE_LABEL,
) -> tuple[str, ...]:
    """Distinct genre buckets in *releases*, sorted case-insensitively."""
    by_key: dict[str, str] = {}
    for release in releases:
        label = release_genre_bucket(release, no_genre_label=no_genre_label)
        key = label.casefold()
        by_key.setdefault(key, label)
    return tuple(sorted(by_key.values(), key=lambda item: item.casefold()))


def prune_enabled_genres(
    enabled_genres: frozenset[str],
    available_genres: tuple[str, ...] | frozenset[str],
) -> frozenset[str]:
    available = frozenset(available_genres)
    return frozenset(genre for genre in enabled_genres if genre in available)


def release_type_filter_bucket(release: Release) -> str:
    if release.release_type == ReleaseType.ALBUM:
        return RELEASE_TYPE_FILTER_ALBUM
    if release.release_type == ReleaseType.SINGLE:
        return RELEASE_TYPE_FILTER_SINGLE
    if release.release_type == ReleaseType.EP:
        return RELEASE_TYPE_FILTER_EP
    return RELEASE_TYPE_FILTER_OTHER


def apply_release_type_filter(
    releases: list[Release],
    enabled_release_types: frozenset[str],
) -> list[Release]:
    if not enabled_release_types:
        return list(releases)
    return [
        release
        for release in releases
        if release_type_filter_bucket(release) in enabled_release_types
    ]


def apply_genre_filter(
    releases: list[Release],
    enabled_genres: frozenset[str],
    *,
    no_genre_label: str = NO_GENRE_LABEL,
) -> list[Release]:
    if not enabled_genres:
        return list(releases)
    return [
        release
        for release in releases
        if release_genre_bucket(release, no_genre_label=no_genre_label) in enabled_genres
    ]


_QUALITY_TIER_ORDER: tuple[str, ...] = (
    QUALITY_FILTER_COMPRESSED,
    QUALITY_FILTER_CD,
    QUALITY_FILTER_HI_RES,
)


def quality_tiers_in_selection(
    releases: list[Release] | tuple[Release, ...],
) -> tuple[str, ...]:
    """Distinct quality tiers in *releases*, in stable bucket order."""
    present: set[str] = set()
    for release in releases:
        present.update(release_available_quality_tiers(release))
    return tuple(tier for tier in _QUALITY_TIER_ORDER if tier in present)


def prune_enabled_quality_tiers(
    enabled_quality_tiers: frozenset[str],
    available_tiers: tuple[str, ...] | frozenset[str],
) -> frozenset[str]:
    available = frozenset(available_tiers)
    return frozenset(tier for tier in enabled_quality_tiers if tier in available)


def apply_quality_filter(
    releases: list[Release],
    enabled_quality_tiers: frozenset[str],
) -> list[Release]:
    if not enabled_quality_tiers:
        return list(releases)
    return [
        release
        for release in releases
        if release_matches_quality_filter(release, enabled_quality_tiers)
    ]


def _year_sort_key(release: Release, *, sort_descending: bool) -> tuple:
    title_key = release.title.casefold()
    if release.year is None:
        return (1, 0, title_key, release.id)
    year = release.year
    if sort_descending:
        return (0, -year, title_key, release.id)
    return (0, year, title_key, release.id)


def _text_sort_key(release: Release, *, sort_key: str) -> tuple:
    title_key = release.title.casefold()
    if sort_key == SORT_KEY_ALBUM:
        return (title_key, release.id)
    if sort_key == SORT_KEY_ARTIST:
        return (release.artist_name.casefold(), title_key, release.id)
    if sort_key == SORT_KEY_SOURCE:
        return (release.source.value, title_key, release.id)
    return (title_key, release.id)


def apply_shell_sort(
    releases: list[Release],
    *,
    sort_key: str | None,
    sort_descending: bool,
) -> list[Release]:
    if not sort_key or sort_key not in _VALID_SORT_KEYS:
        return list(releases)
    if sort_key == SORT_KEY_YEAR:
        return sorted(
            releases,
            key=lambda release: _year_sort_key(release, sort_descending=sort_descending),
        )
    # Down arrow (sort_descending=True): A→Z; up arrow: Z→A. Year uses the opposite convention.
    return sorted(
        releases,
        key=lambda release: _text_sort_key(release, sort_key=sort_key),
        reverse=not sort_descending,
    )


def apply_shell_view_filters(
    releases: list[Release],
    *,
    enabled_sources: frozenset[Source],
    enabled_genres: frozenset[str],
    enabled_release_types: frozenset[str] | None = None,
    enabled_quality_tiers: frozenset[str] | None = None,
    available_sources: frozenset[Source] | None = None,
    sort_key: str | None = None,
    sort_descending: bool = True,
) -> list[Release]:
    filtered = apply_source_filter(
        releases,
        enabled_sources,
        available_sources=available_sources,
    )
    filtered = apply_release_type_filter(
        filtered,
        enabled_release_types or frozenset(),
    )
    filtered = apply_genre_filter(filtered, enabled_genres)
    filtered = apply_quality_filter(
        filtered,
        enabled_quality_tiers or frozenset(),
    )
    return apply_shell_sort(
        filtered,
        sort_key=sort_key,
        sort_descending=sort_descending,
    )


def releases_from_recently_added(items: list[RecentlyAddedItem]) -> list[Release]:
    """Sort by added_ns descending, then title; return releases only."""
    ordered = sorted(
        items,
        key=lambda item: (-item.added_ns, item.release.title.casefold()),
    )
    return [item.release for item in ordered]
