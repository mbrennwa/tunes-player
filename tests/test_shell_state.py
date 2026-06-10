"""Shell state persistence and source filtering."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tunes_player.core.config import ConfigManager
from tunes_player.core.models import Release, ReleaseType, Source
from tunes_player.core.release_quality import QUALITY_FILTER_CD
from tunes_player.core.shell_state import (
    SearchScope,
    NO_GENRE_LABEL,
    QUALITY_FILTER_COMPRESSED,
    QUALITY_FILTER_HI_RES,
    RELEASE_TYPE_FILTER_ALBUM,
    RELEASE_TYPE_FILTER_EP,
    RELEASE_TYPE_FILTER_OTHER,
    RELEASE_TYPE_FILTER_SINGLE,
    SORT_KEY_ALBUM,
    SORT_KEY_ARTIST,
    SORT_KEY_SOURCE,
    SORT_KEY_YEAR,
    ShellBase,
    ShellState,
    apply_genre_filter,
    apply_quality_filter,
    apply_release_type_filter,
    apply_shell_sort,
    apply_source_filter,
    cached_releases_compatible_with_available,
    ensure_source_enabled,
    filter_releases_to_available_sources,
    prune_enabled_sources,
    genres_in_selection,
    parse_shell_state,
    prune_enabled_genres,
    cached_releases_have_quality_tiers,
    prune_enabled_quality_tiers,
    refresh_local_peak_quality_tiers,
    refresh_local_release_art_uris,
    quality_tiers_in_selection,
    release_from_cache_payload,
    release_to_cache_payload,
    release_type_filter_bucket,
    releases_from_cache_payloads,
)


def _release(
    release_id: str,
    source: Source,
    *,
    title: str = "Title",
    artist_name: str = "Artist",
    year: int | None = 2024,
    genre: str | None = "Rock",
    release_type: ReleaseType = ReleaseType.ALBUM,
    peak_quality_tier: str = QUALITY_FILTER_CD,
    available_quality_tiers: frozenset[str] | None = None,
) -> Release:
    release = Release(
        id=release_id,
        title=title,
        artist_name=artist_name,
        source=source,
        year=year,
        genre=genre,
        release_type=release_type,
        peak_quality_tier=peak_quality_tier,
        available_quality_tiers=frozenset(),
    )
    if available_quality_tiers is None:
        from tunes_player.core.release_quality import release_available_quality_tiers

        available_quality_tiers = release_available_quality_tiers(release)
    return replace(release, available_quality_tiers=available_quality_tiers)


class TestShellStateParsing(unittest.TestCase):
    def test_defaults(self) -> None:
        state = parse_shell_state(None)
        self.assertEqual(state.base, ShellBase.NONE)
        self.assertEqual(state.search_query, "")
        self.assertEqual(state.search_scope, SearchScope.ALL)
        self.assertEqual(state.enabled_sources, frozenset())
        self.assertEqual(state.enabled_genres, frozenset())
        self.assertEqual(state.enabled_release_types, frozenset())
        self.assertEqual(state.enabled_quality_tiers, frozenset())
        self.assertIsNone(state.sort_key)
        self.assertTrue(state.sort_descending)
        self.assertEqual(state.cached_releases, ())

    def test_roundtrip_dict(self) -> None:
        state = ShellState(
            base=ShellBase.SEARCH,
            search_query="beatles",
            enabled_sources=frozenset({Source.TIDAL, Source.LOCAL}),
            enabled_genres=frozenset({"Jazz", NO_GENRE_LABEL}),
        )
        restored = ShellState.from_dict(state.to_dict())
        self.assertEqual(restored, state)

    def test_search_query_cleared_when_not_search_base(self) -> None:
        restored = ShellState.from_dict(
            {"base": "new_music", "search_query": "leftover"},
        )
        self.assertEqual(restored.base, ShellBase.NEW_MUSIC)
        self.assertEqual(restored.search_query, "")
        self.assertEqual(restored.search_scope, SearchScope.ALL)

    def test_artist_search_scope_roundtrip(self) -> None:
        state = ShellState(
            base=ShellBase.SEARCH,
            search_query="Björk",
            search_scope=SearchScope.ARTIST,
        )
        restored = ShellState.from_dict(state.to_dict())
        self.assertEqual(restored.search_scope, SearchScope.ARTIST)

    def test_all_local_roundtrip(self) -> None:
        state = ShellState(base=ShellBase.ALL_LOCAL)
        restored = ShellState.from_dict(state.to_dict())
        self.assertEqual(restored.base, ShellBase.ALL_LOCAL)
        self.assertEqual(restored.search_query, "")

    def test_legacy_source_filter(self) -> None:
        restored = ShellState.from_dict({"source_filter": "qobuz"})
        self.assertEqual(restored.enabled_sources, frozenset({Source.QOBUZ}))

    def test_enabled_release_types_roundtrip(self) -> None:
        state = ShellState(
            enabled_release_types=frozenset({RELEASE_TYPE_FILTER_EP, RELEASE_TYPE_FILTER_SINGLE}),
        )
        restored = ShellState.from_dict(state.to_dict())
        self.assertEqual(restored.enabled_release_types, state.enabled_release_types)

    def test_enabled_quality_tiers_roundtrip(self) -> None:
        state = ShellState(
            enabled_quality_tiers=frozenset({QUALITY_FILTER_CD, QUALITY_FILTER_HI_RES}),
        )
        restored = ShellState.from_dict(state.to_dict())
        self.assertEqual(restored.enabled_quality_tiers, state.enabled_quality_tiers)

    def test_sort_state_roundtrip(self) -> None:
        state = ShellState(
            sort_key=SORT_KEY_YEAR,
            sort_descending=False,
        )
        restored = ShellState.from_dict(state.to_dict())
        self.assertEqual(restored.sort_key, SORT_KEY_YEAR)
        self.assertFalse(restored.sort_descending)

    def test_sort_descending_only_persisted_when_not_default(self) -> None:
        state = ShellState(sort_key=SORT_KEY_ALBUM, sort_descending=True)
        payload = state.to_dict()
        self.assertEqual(payload["sort_key"], SORT_KEY_ALBUM)
        self.assertNotIn("sort_descending", payload)

        state_asc = ShellState(sort_key=SORT_KEY_ALBUM, sort_descending=False)
        payload_asc = state_asc.to_dict()
        self.assertFalse(payload_asc["sort_descending"])


class TestApplyShellSort(unittest.TestCase):
    def test_none_preserves_input_order(self) -> None:
        releases = [
            _release("c", Source.LOCAL),
            _release("a", Source.TIDAL),
            _release("b", Source.QOBUZ),
        ]
        sorted_releases = apply_shell_sort(
            releases,
            sort_key=None,
            sort_descending=True,
        )
        self.assertEqual([r.id for r in sorted_releases], ["c", "a", "b"])

    def test_year_descending_missing_years_last(self) -> None:
        releases = [
            _release("old", Source.LOCAL, year=1990, title="B"),
            _release("none", Source.LOCAL, year=None, title="A"),
            _release("new", Source.LOCAL, year=2020, title="C"),
        ]
        ordered = apply_shell_sort(
            releases,
            sort_key=SORT_KEY_YEAR,
            sort_descending=True,
        )
        self.assertEqual([r.id for r in ordered], ["new", "old", "none"])

    def test_year_ascending_missing_years_last(self) -> None:
        releases = [
            _release("new", Source.LOCAL, year=2020),
            _release("none", Source.LOCAL, year=None),
            _release("old", Source.LOCAL, year=1990),
        ]
        ordered = apply_shell_sort(
            releases,
            sort_key=SORT_KEY_YEAR,
            sort_descending=False,
        )
        self.assertEqual([r.id for r in ordered], ["old", "new", "none"])

    def test_album_case_insensitive(self) -> None:
        releases = [
            _release("b", Source.LOCAL, title="beta"),
            _release("a", Source.LOCAL, title="Alpha"),
        ]
        ordered = apply_shell_sort(
            releases,
            sort_key=SORT_KEY_ALBUM,
            sort_descending=True,
        )
        self.assertEqual([r.id for r in ordered], ["a", "b"])

    def test_artist_case_insensitive(self) -> None:
        releases = [
            _release("b", Source.LOCAL, artist_name="zebra"),
            _release("a", Source.LOCAL, artist_name="Apple"),
        ]
        ordered = apply_shell_sort(
            releases,
            sort_key=SORT_KEY_ARTIST,
            sort_descending=True,
        )
        self.assertEqual([r.id for r in ordered], ["a", "b"])

    def test_album_descending_z_to_a(self) -> None:
        releases = [
            _release("a", Source.LOCAL, title="Alpha"),
            _release("b", Source.LOCAL, title="beta"),
        ]
        ordered = apply_shell_sort(
            releases,
            sort_key=SORT_KEY_ALBUM,
            sort_descending=False,
        )
        self.assertEqual([r.id for r in ordered], ["b", "a"])

    def test_source_ordering(self) -> None:
        releases = [
            _release("t", Source.TIDAL),
            _release("l", Source.LOCAL),
            _release("q", Source.QOBUZ),
        ]
        ordered = apply_shell_sort(
            releases,
            sort_key=SORT_KEY_SOURCE,
            sort_descending=True,
        )
        self.assertEqual([r.id for r in ordered], ["l", "q", "t"])


class TestReleaseTypeFilter(unittest.TestCase):
    def test_buckets(self) -> None:
        self.assertEqual(
            release_type_filter_bucket(_release("a", Source.LOCAL)),
            RELEASE_TYPE_FILTER_ALBUM,
        )
        self.assertEqual(
            release_type_filter_bucket(
                _release("s", Source.LOCAL, release_type=ReleaseType.SINGLE),
            ),
            RELEASE_TYPE_FILTER_SINGLE,
        )
        self.assertEqual(
            release_type_filter_bucket(
                _release("e", Source.LOCAL, release_type=ReleaseType.EP),
            ),
            RELEASE_TYPE_FILTER_EP,
        )
        self.assertEqual(
            release_type_filter_bucket(
                _release("x", Source.LOCAL, release_type=ReleaseType.SYNTHETIC),
            ),
            RELEASE_TYPE_FILTER_OTHER,
        )

    def test_apply_release_type_filter(self) -> None:
        releases = [
            _release("a", Source.LOCAL, release_type=ReleaseType.ALBUM),
            _release("s", Source.TIDAL, release_type=ReleaseType.SINGLE),
            _release("e", Source.QOBUZ, release_type=ReleaseType.EP),
        ]
        filtered = apply_release_type_filter(
            releases,
            frozenset({RELEASE_TYPE_FILTER_SINGLE, RELEASE_TYPE_FILTER_EP}),
        )
        self.assertEqual([r.id for r in filtered], ["s", "e"])

    def test_empty_filter_is_passthrough(self) -> None:
        releases = [_release("a", Source.LOCAL)]
        self.assertEqual(len(apply_release_type_filter(releases, frozenset())), 1)


class TestReleaseCachePayload(unittest.TestCase):
    def test_roundtrip(self) -> None:
        release = _release("tidal:99", Source.TIDAL)
        payload = release_to_cache_payload(release)
        restored = release_from_cache_payload(payload)
        assert restored is not None
        self.assertEqual(restored, release)

    def test_cache_payload_always_includes_peak_quality_tier(self) -> None:
        payload = release_to_cache_payload(_release("local:1", Source.LOCAL))
        self.assertEqual(payload["peak_quality_tier"], QUALITY_FILTER_CD)

    def test_cache_payload_roundtrips_available_quality_tiers(self) -> None:
        release = _release(
            "qobuz:1",
            Source.QOBUZ,
            peak_quality_tier=QUALITY_FILTER_CD,
            available_quality_tiers=frozenset(
                {
                    QUALITY_FILTER_COMPRESSED,
                    QUALITY_FILTER_CD,
                    QUALITY_FILTER_HI_RES,
                },
            ),
        )
        payload = release_to_cache_payload(release)
        restored = release_from_cache_payload(payload)
        assert restored is not None
        self.assertEqual(restored.available_quality_tiers, release.available_quality_tiers)

    def test_cached_releases_have_quality_tiers(self) -> None:
        self.assertFalse(cached_releases_have_quality_tiers(()))
        payload = release_to_cache_payload(
            _release("a", Source.LOCAL, peak_quality_tier=QUALITY_FILTER_HI_RES),
        )
        self.assertTrue(cached_releases_have_quality_tiers((payload,)))
        legacy = {"id": "local:1", "title": "T", "artist_name": "A", "source": "local"}
        self.assertFalse(cached_releases_have_quality_tiers((legacy,)))

    def test_releases_from_cache_payloads(self) -> None:
        payloads = (
            release_to_cache_payload(_release("local:1", Source.LOCAL)),
            release_to_cache_payload(_release("tidal:2", Source.TIDAL)),
        )
        restored = releases_from_cache_payloads(payloads)
        self.assertEqual(len(restored), 2)
        self.assertEqual(restored[0].id, "local:1")


class TestGenreSelectionHelpers(unittest.TestCase):
    def test_genres_in_selection_dedupes_case(self) -> None:
        releases = [
            _release("local:1", Source.LOCAL, genre="Jazz"),
            _release("local:2", Source.LOCAL, genre="jazz"),
            _release("local:3", Source.LOCAL, genre=None),
        ]
        self.assertEqual(
            genres_in_selection(releases),
            (NO_GENRE_LABEL, "Jazz"),
        )

    def test_apply_genre_filter_empty_is_passthrough(self) -> None:
        releases = [
            _release("local:1", Source.LOCAL, genre="Rock"),
            _release("local:2", Source.LOCAL, genre="Jazz"),
        ]
        self.assertEqual(len(apply_genre_filter(releases, frozenset())), 2)

    def test_apply_genre_filter_matches_buckets(self) -> None:
        releases = [
            _release("local:1", Source.LOCAL, genre="Rock"),
            _release("local:2", Source.LOCAL, genre="Jazz"),
            _release("tidal:1", Source.TIDAL, genre=None),
        ]
        filtered = apply_genre_filter(releases, frozenset({"Jazz", NO_GENRE_LABEL}))
        self.assertEqual([r.id for r in filtered], ["local:2", "tidal:1"])

    def test_prune_enabled_genres(self) -> None:
        pruned = prune_enabled_genres(
            frozenset({"Rock", "Stale"}),
            ("Rock", "Jazz"),
        )
        self.assertEqual(pruned, frozenset({"Rock"}))


class TestRefreshLocalPeakQualityTiers(unittest.TestCase):
    def test_replaces_stale_cached_tier(self) -> None:
        releases = [
            _release("local:1", Source.LOCAL, peak_quality_tier=QUALITY_FILTER_COMPRESSED),
            _release("tidal:1", Source.TIDAL, peak_quality_tier=QUALITY_FILTER_COMPRESSED),
        ]
        refreshed = refresh_local_peak_quality_tiers(
            releases,
            local_tier_by_id={"local:1": QUALITY_FILTER_HI_RES},
        )
        self.assertEqual(refreshed[0].peak_quality_tier, QUALITY_FILTER_HI_RES)
        self.assertEqual(refreshed[1].peak_quality_tier, QUALITY_FILTER_COMPRESSED)


class TestRefreshLocalReleaseArtUris(unittest.TestCase):
    def test_updates_local_only(self) -> None:
        streaming_uri = "https://example.com/cover.jpg"
        local_uri = "tunes://art/local/local%3Aalbum%3Aabc"
        releases = [
            replace(_release("local:1", Source.LOCAL), art_uri=None),
            replace(_release("qobuz:1", Source.QOBUZ), art_uri=streaming_uri),
        ]
        refreshed = refresh_local_release_art_uris(
            releases,
            local_art_by_id={"local:1": local_uri, "qobuz:1": None},
        )
        self.assertEqual(refreshed[0].art_uri, local_uri)
        self.assertEqual(refreshed[1].art_uri, streaming_uri)

    def test_none_for_unindexed_local(self) -> None:
        releases = [
            replace(_release("local:1", Source.LOCAL), art_uri="tunes://art/local/stale"),
        ]
        refreshed = refresh_local_release_art_uris(
            releases,
            local_art_by_id={"local:1": None},
        )
        self.assertIsNone(refreshed[0].art_uri)


class TestQualityFilter(unittest.TestCase):
    def test_quality_tiers_in_selection(self) -> None:
        releases = [
            _release("a", Source.LOCAL, peak_quality_tier=QUALITY_FILTER_CD),
            _release("b", Source.TIDAL, peak_quality_tier=QUALITY_FILTER_HI_RES),
            _release("c", Source.QOBUZ, peak_quality_tier=QUALITY_FILTER_CD),
        ]
        self.assertEqual(
            quality_tiers_in_selection(releases),
            (
                QUALITY_FILTER_COMPRESSED,
                QUALITY_FILTER_CD,
                QUALITY_FILTER_HI_RES,
            ),
        )

    def test_apply_quality_filter(self) -> None:
        releases = [
            _release("a", Source.LOCAL, peak_quality_tier=QUALITY_FILTER_COMPRESSED),
            _release("b", Source.TIDAL, peak_quality_tier=QUALITY_FILTER_CD),
            _release("c", Source.QOBUZ, peak_quality_tier=QUALITY_FILTER_HI_RES),
        ]
        filtered = apply_quality_filter(
            releases,
            frozenset({QUALITY_FILTER_CD, QUALITY_FILTER_HI_RES}),
        )
        self.assertEqual([r.id for r in filtered], ["b", "c"])

    def test_empty_quality_filter_is_passthrough(self) -> None:
        releases = [_release("a", Source.LOCAL)]
        self.assertEqual(len(apply_quality_filter(releases, frozenset())), 1)

    def test_apply_quality_filter_matches_available_not_peak_only(self) -> None:
        releases = [
            _release(
                "dual",
                Source.QOBUZ,
                peak_quality_tier=QUALITY_FILTER_CD,
                available_quality_tiers=frozenset(
                    {
                        QUALITY_FILTER_COMPRESSED,
                        QUALITY_FILTER_CD,
                        QUALITY_FILTER_HI_RES,
                    },
                ),
            ),
        ]
        filtered = apply_quality_filter(
            releases,
            frozenset({QUALITY_FILTER_HI_RES}),
        )
        self.assertEqual([r.id for r in filtered], ["dual"])

    def test_apply_quality_filter_excludes_unknown_tier(self) -> None:
        releases = [
            _release("a", Source.TIDAL, peak_quality_tier=QUALITY_FILTER_COMPRESSED),
            _release("b", Source.TIDAL, peak_quality_tier=""),
        ]
        filtered = apply_quality_filter(
            releases,
            frozenset({QUALITY_FILTER_COMPRESSED}),
        )
        self.assertEqual([r.id for r in filtered], ["a"])

    def test_prune_enabled_quality_tiers(self) -> None:
        pruned = prune_enabled_quality_tiers(
            frozenset({QUALITY_FILTER_CD, QUALITY_FILTER_COMPRESSED}),
            (QUALITY_FILTER_CD,),
        )
        self.assertEqual(pruned, frozenset({QUALITY_FILTER_CD}))


class TestEnsureSourceEnabled(unittest.TestCase):
    def test_all_sources_unchanged(self) -> None:
        available = {Source.LOCAL, Source.TIDAL}
        self.assertEqual(
            ensure_source_enabled(frozenset(), Source.LOCAL, available=available),
            frozenset(),
        )

    def test_adds_missing_source(self) -> None:
        available = {Source.LOCAL, Source.TIDAL, Source.QOBUZ}
        self.assertEqual(
            ensure_source_enabled(
                frozenset({Source.TIDAL}),
                Source.LOCAL,
                available=available,
            ),
            frozenset({Source.TIDAL, Source.LOCAL}),
        )

    def test_already_enabled_unchanged(self) -> None:
        available = {Source.LOCAL, Source.TIDAL}
        enabled = frozenset({Source.LOCAL, Source.TIDAL})
        self.assertEqual(
            ensure_source_enabled(enabled, Source.LOCAL, available=available),
            enabled,
        )

    def test_unavailable_source_unchanged(self) -> None:
        enabled = frozenset({Source.TIDAL})
        self.assertEqual(
            ensure_source_enabled(enabled, Source.LOCAL, available={Source.TIDAL}),
            enabled,
        )


class TestApplySourceFilter(unittest.TestCase):
    def test_all_sources(self) -> None:
        releases = [
            _release("local:1", Source.LOCAL),
            _release("tidal:1", Source.TIDAL),
        ]
        self.assertEqual(len(apply_source_filter(releases, frozenset())), 2)

    def test_empty_enabled_respects_available_sources(self) -> None:
        releases = [
            _release("local:1", Source.LOCAL),
            _release("tidal:1", Source.TIDAL),
            _release("qobuz:1", Source.QOBUZ),
        ]
        filtered = apply_source_filter(
            releases,
            frozenset(),
            available_sources=frozenset({Source.TIDAL, Source.QOBUZ}),
        )
        self.assertEqual([r.id for r in filtered], ["tidal:1", "qobuz:1"])

    def test_single_source(self) -> None:
        releases = [
            _release("local:1", Source.LOCAL),
            _release("tidal:1", Source.TIDAL),
        ]
        filtered = apply_source_filter(releases, frozenset({Source.LOCAL}))
        self.assertEqual([r.id for r in filtered], ["local:1"])

    def test_prune_enabled_sources(self) -> None:
        pruned = prune_enabled_sources(
            frozenset({Source.LOCAL, Source.TIDAL}),
            {Source.TIDAL, Source.QOBUZ},
        )
        self.assertEqual(pruned, frozenset({Source.TIDAL}))

    def test_cached_releases_compatible_with_available(self) -> None:
        local_only = (
            release_to_cache_payload(_release("local:1", Source.LOCAL)),
        )
        self.assertFalse(
            cached_releases_compatible_with_available(
                local_only,
                frozenset({Source.TIDAL, Source.QOBUZ}),
            ),
        )
        mixed = (
            release_to_cache_payload(_release("local:1", Source.LOCAL)),
            release_to_cache_payload(_release("tidal:1", Source.TIDAL)),
        )
        self.assertTrue(
            cached_releases_compatible_with_available(
                mixed,
                frozenset({Source.TIDAL}),
            ),
        )

    def test_filter_releases_to_available_sources(self) -> None:
        releases = [
            _release("local:1", Source.LOCAL),
            _release("tidal:1", Source.TIDAL),
        ]
        filtered = filter_releases_to_available_sources(
            releases,
            frozenset({Source.QOBUZ}),
        )
        self.assertEqual(filtered, [])


class TestShellStateConfigPersistence(unittest.TestCase):
    def test_config_roundtrip_with_cached_releases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            manager = ConfigManager(path)
            manager.load()
            release = _release("qobuz:1", Source.QOBUZ)
            state = ShellState(
                base=ShellBase.SUGGESTION,
                enabled_sources=frozenset({Source.QOBUZ}),
                enabled_genres=frozenset({"Rock"}),
                cached_releases=(release_to_cache_payload(release),),
            )
            manager.set_shell_state(state)

            other = ConfigManager(path)
            other.load()
            self.assertEqual(len(other.config.shell_state.cached_releases), 1)
            restored = release_from_cache_payload(other.config.shell_state.cached_releases[0])
            assert restored is not None
            self.assertEqual(restored.id, "qobuz:1")

            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("cached_releases", raw["shell_state"])
            self.assertEqual(raw["shell_state"]["enabled_genres"], ["Rock"])


if __name__ == "__main__":
    unittest.main()
