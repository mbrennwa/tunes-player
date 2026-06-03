"""Shell UI state: base selection, search query, and source filter."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from tunes_player.core.home import RecentlyAddedItem
from tunes_player.core.models import Release, Source


class ShellBase(str, Enum):
    NONE = "none"
    SEARCH = "search"
    NEW_MUSIC = "new_music"
    SUGGESTION = "suggestion"


_VALID_BASES = frozenset(item.value for item in ShellBase)
_VALID_SOURCES = frozenset(item.value for item in Source)


@dataclass(frozen=True, slots=True)
class ShellState:
    base: ShellBase = ShellBase.NONE
    search_query: str = ""
    # Empty set = all configured sources enabled. Non-empty = only those sources.
    enabled_sources: frozenset[Source] = field(default_factory=frozenset)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "base": self.base.value,
            "search_query": self.search_query,
        }
        if self.enabled_sources:
            payload["enabled_sources"] = sorted(
                source.value for source in self.enabled_sources
            )
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
        if base != ShellBase.SEARCH:
            search_query = ""
        enabled_sources = _parse_enabled_sources(raw)
        return cls(
            base=base,
            search_query=search_query,
            enabled_sources=enabled_sources,
        )


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
    # Legacy single-source filter.
    filter_raw = raw.get("source_filter")
    if isinstance(filter_raw, str) and filter_raw in _VALID_SOURCES:
        return frozenset({Source(filter_raw)})
    return frozenset()


def parse_shell_state(raw: object) -> ShellState:
    return ShellState.from_dict(raw)


def apply_source_filter(
    releases: list[Release],
    enabled_sources: frozenset[Source],
) -> list[Release]:
    if not enabled_sources:
        return list(releases)
    return [release for release in releases if release.source in enabled_sources]


def releases_from_recently_added(items: list[RecentlyAddedItem]) -> list[Release]:
    """Sort by added_ns descending, then title; return releases only."""
    ordered = sorted(
        items,
        key=lambda item: (-item.added_ns, item.release.title.casefold()),
    )
    return [item.release for item in ordered]
