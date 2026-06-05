"""Fetch and filter release selections for the music shell."""

from __future__ import annotations

from tunes_player.core.models import Release, Source
from tunes_player.core.release_quality import (
    QUALITY_FILTER_CD,
    QUALITY_FILTER_COMPRESSED,
    QUALITY_FILTER_HI_RES,
)
from tunes_player.core.services import PlayerService
from tunes_player.core.shell_state import (
    ShellBase,
    ShellState,
    apply_shell_view_filters,
    releases_from_recently_added,
)

_QUALITY_FILTER_LABELS = {
    QUALITY_FILTER_COMPRESSED: "Compressed",
    QUALITY_FILTER_CD: "CD",
    QUALITY_FILTER_HI_RES: "Hi-res",
}

_SOURCE_FILTER_LABELS = {
    Source.LOCAL: "Local",
    Source.TIDAL: "TIDAL",
    Source.QOBUZ: "Qobuz",
}


def available_sources(service: PlayerService) -> set[Source]:
    sources: set[Source] = set()
    if service.config.config.music_folders:
        sources.add(Source.LOCAL)
    if service.tidal_is_logged_in():
        sources.add(Source.TIDAL)
    if service.qobuz_is_logged_in():
        sources.add(Source.QOBUZ)
    return sources


def all_local_empty_message(
    service: PlayerService,
    *,
    has_unfiltered_releases: bool,
) -> str | None:
    """Placeholder copy for an empty All Local grid."""
    if not service.config.config.music_folders:
        return (
            "No local music yet.\n"
            "Add folders in Settings → Sources, then scan your library."
        )
    if not has_unfiltered_releases:
        return (
            "No local music scanned yet.\n"
            "Open Settings → Sources and turn on Watch folder for your libraries."
        )
    return None


def filter_empty_message(state: ShellState) -> str | None:
    """Explain an empty grid when the catalog still has releases."""
    if state.enabled_genres:
        return "No releases match the selected genres."
    if state.enabled_release_types:
        return "No releases match the selected release types."
    if state.enabled_quality_tiers:
        labels = ", ".join(
            _QUALITY_FILTER_LABELS.get(tier, tier)
            for tier in sorted(state.enabled_quality_tiers)
        )
        return f"No releases match the selected quality ({labels})."
    if state.enabled_sources:
        names = ", ".join(
            _SOURCE_FILTER_LABELS.get(source, source.value.capitalize())
            for source in sorted(state.enabled_sources, key=lambda item: item.value)
        )
        return f"No releases from {names} in this selection."
    return None


def empty_grid_message(
    service: PlayerService,
    state: ShellState,
    *,
    catalog_count: int,
) -> str | None:
    """User-facing placeholder when a filtered grid has no rows."""
    if catalog_count > 0:
        return filter_empty_message(state) or "No releases match the current filters."

    if state.base == ShellBase.ALL_LOCAL:
        return all_local_empty_message(service, has_unfiltered_releases=False)

    if state.base == ShellBase.NEW_MUSIC:
        days = service.config.config.new_music_within_days
        return (
            f"Nothing new in the last {days} days.\n"
            "Add music folders or sign in to TIDAL or Qobuz in Settings → Sources."
        )

    if state.base == ShellBase.SUGGESTION:
        return (
            "Play music to build suggestions from your library, or sign in to "
            "TIDAL or Qobuz in Settings → Sources."
        )

    if state.base == ShellBase.SEARCH and state.search_query.strip():
        return f'No results for “{state.search_query}”.'

    return filter_empty_message(state)


def fetch_base_releases(
    service: PlayerService,
    base: ShellBase,
    *,
    search_query: str = "",
) -> list[Release]:
    if base == ShellBase.NONE:
        return []
    if base == ShellBase.SEARCH:
        needle = search_query.strip()
        if not needle:
            return []
        return list(service.search(needle).releases)
    if base == ShellBase.NEW_MUSIC:
        return releases_from_recently_added(service.list_recently_added_items())
    if base == ShellBase.SUGGESTION:
        return releases_from_recently_added(service.list_suggestion_items())
    if base == ShellBase.ALL_LOCAL:
        if not service.config.config.music_folders:
            return []
        return service.list_releases()
    return []


def fetch_filtered_releases(
    service: PlayerService,
    base: ShellBase,
    *,
    search_query: str = "",
    enabled_sources: frozenset[Source] | None = None,
    enabled_genres: frozenset[str] | None = None,
    enabled_release_types: frozenset[str] | None = None,
    enabled_quality_tiers: frozenset[str] | None = None,
    sort_key: str | None = None,
    sort_descending: bool = True,
) -> list[Release]:
    releases = fetch_base_releases(service, base, search_query=search_query)
    return apply_shell_view_filters(
        releases,
        enabled_sources=enabled_sources or frozenset(),
        enabled_genres=enabled_genres or frozenset(),
        enabled_release_types=enabled_release_types or frozenset(),
        enabled_quality_tiers=enabled_quality_tiers or frozenset(),
        available_sources=frozenset(available_sources(service)),
        sort_key=sort_key,
        sort_descending=sort_descending,
    )
