"""Fetch and filter release selections for the music shell."""

from __future__ import annotations

from tunes_player.core.models import Release, Source
from tunes_player.core.services import PlayerService
from tunes_player.core.shell_state import (
    ShellBase,
    apply_shell_view_filters,
    releases_from_recently_added,
)


def available_sources(service: PlayerService) -> set[Source]:
    sources: set[Source] = set()
    if service.config.config.music_folders:
        sources.add(Source.LOCAL)
    if service.tidal_is_logged_in():
        sources.add(Source.TIDAL)
    if service.qobuz_is_logged_in():
        sources.add(Source.QOBUZ)
    return sources


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
    return []


def fetch_filtered_releases(
    service: PlayerService,
    base: ShellBase,
    *,
    search_query: str = "",
    enabled_sources: frozenset[Source] | None = None,
    enabled_genres: frozenset[str] | None = None,
) -> list[Release]:
    releases = fetch_base_releases(service, base, search_query=search_query)
    return apply_shell_view_filters(
        releases,
        enabled_sources=enabled_sources or frozenset(),
        enabled_genres=enabled_genres or frozenset(),
    )
