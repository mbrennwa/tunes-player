"""Tests for catalog quality enrich scope and visible-grid stability."""

from __future__ import annotations

import unittest
from dataclasses import replace

from tunes_player.core.models import Release, ReleaseType, Source
from tunes_player.core.release_quality import QUALITY_FILTER_CD, QUALITY_FILTER_HI_RES
from tunes_player.core.shell_state import (
    apply_catalog_enrich_scope_filters,
    apply_shell_view_filters,
)


def _release(
    release_id: str,
    source: Source,
    *,
    catalog_quality_ready: bool = True,
    peak_quality_tier: str = QUALITY_FILTER_CD,
    available_quality_tiers: frozenset[str] | None = None,
) -> Release:
    if available_quality_tiers is None and peak_quality_tier:
        available_quality_tiers = frozenset({peak_quality_tier})
    return Release(
        id=release_id,
        title="Title",
        artist_name="Artist",
        source=source,
        year=2024,
        genre="Rock",
        release_type=ReleaseType.ALBUM,
        peak_quality_tier=peak_quality_tier,
        available_quality_tiers=available_quality_tiers or frozenset(),
        catalog_quality_ready=catalog_quality_ready,
        catalog_release_id=release_id,
    )


def _filtered_release_ids(
    releases: list[Release],
    *,
    enabled_sources: frozenset[Source],
    enabled_quality_tiers: frozenset[str] = frozenset(),
    available_sources: frozenset[Source],
) -> tuple[str, ...]:
    filtered = apply_shell_view_filters(
        releases,
        enabled_sources=enabled_sources,
        enabled_genres=frozenset(),
        enabled_release_types=frozenset(),
        enabled_quality_tiers=enabled_quality_tiers,
        available_sources=available_sources,
    )
    return tuple(release.id for release in filtered)


class CatalogEnrichScopeTests(unittest.TestCase):
    def test_enrich_scope_excludes_disabled_sources(self) -> None:
        local = _release("local:1", Source.LOCAL)
        tidal = _release(
            "tidal:album:1",
            Source.TIDAL,
            catalog_quality_ready=False,
            peak_quality_tier="",
        )
        available = frozenset({Source.LOCAL, Source.TIDAL})
        scoped = apply_catalog_enrich_scope_filters(
            [local, tidal],
            enabled_sources=frozenset({Source.LOCAL}),
            enabled_genres=frozenset(),
            enabled_release_types=frozenset(),
            available_sources=available,
        )
        self.assertEqual([release.id for release in scoped], ["local:1"])

    def test_enrich_scope_includes_streaming_when_source_enabled(self) -> None:
        tidal = _release(
            "tidal:album:1",
            Source.TIDAL,
            catalog_quality_ready=False,
            peak_quality_tier="",
        )
        available = frozenset({Source.TIDAL})
        scoped = apply_catalog_enrich_scope_filters(
            [tidal],
            enabled_sources=frozenset({Source.TIDAL}),
            enabled_genres=frozenset(),
            enabled_release_types=frozenset(),
            available_sources=available,
        )
        self.assertEqual([release.id for release in scoped], ["tidal:album:1"])

    def test_enrich_scope_ignores_quality_filter(self) -> None:
        tidal_stub = _release(
            "tidal:album:1",
            Source.TIDAL,
            catalog_quality_ready=False,
            peak_quality_tier="",
            available_quality_tiers=frozenset(),
        )
        available = frozenset({Source.TIDAL})
        scoped = apply_catalog_enrich_scope_filters(
            [tidal_stub],
            enabled_sources=frozenset(),
            enabled_genres=frozenset(),
            enabled_release_types=frozenset(),
            available_sources=available,
        )
        self.assertEqual(len(scoped), 1)
        display = apply_shell_view_filters(
            [tidal_stub],
            enabled_sources=frozenset(),
            enabled_genres=frozenset(),
            enabled_release_types=frozenset(),
            enabled_quality_tiers=frozenset({QUALITY_FILTER_HI_RES}),
            available_sources=available,
        )
        self.assertEqual(display, [])

    def test_visible_ids_unchanged_when_hidden_streaming_enriched(self) -> None:
        local = _release("local:1", Source.LOCAL)
        tidal_stub = _release(
            "tidal:album:1",
            Source.TIDAL,
            catalog_quality_ready=False,
            peak_quality_tier="",
            available_quality_tiers=frozenset(),
        )
        tidal_enriched = replace(
            tidal_stub,
            catalog_quality_ready=True,
            peak_quality_tier=QUALITY_FILTER_CD,
            available_quality_tiers=frozenset({QUALITY_FILTER_CD}),
        )
        available = frozenset({Source.LOCAL, Source.TIDAL})
        enabled_sources = frozenset({Source.LOCAL})

        ids_before = _filtered_release_ids(
            [local, tidal_stub],
            enabled_sources=enabled_sources,
            available_sources=available,
        )
        ids_after = _filtered_release_ids(
            [local, tidal_enriched],
            enabled_sources=enabled_sources,
            available_sources=available,
        )
        self.assertEqual(ids_before, ("local:1",))
        self.assertEqual(ids_after, ids_before)


if __name__ == "__main__":
    unittest.main()
