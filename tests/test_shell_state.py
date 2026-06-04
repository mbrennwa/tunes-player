"""Shell state persistence and source filtering."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tunes_player.core.config import ConfigManager
from tunes_player.core.models import Release, ReleaseType, Source
from tunes_player.core.shell_state import (
    NO_GENRE_LABEL,
    RELEASE_TYPE_FILTER_ALBUM,
    RELEASE_TYPE_FILTER_EP,
    RELEASE_TYPE_FILTER_OTHER,
    RELEASE_TYPE_FILTER_SINGLE,
    ShellBase,
    ShellState,
    apply_genre_filter,
    apply_release_type_filter,
    apply_source_filter,
    genres_in_selection,
    parse_shell_state,
    prune_enabled_genres,
    release_from_cache_payload,
    release_to_cache_payload,
    release_type_filter_bucket,
    releases_from_cache_payloads,
)


def _release(
    release_id: str,
    source: Source,
    *,
    genre: str | None = "Rock",
    release_type: ReleaseType = ReleaseType.ALBUM,
) -> Release:
    return Release(
        id=release_id,
        title="Title",
        artist_name="Artist",
        source=source,
        year=2024,
        genre=genre,
        release_type=release_type,
    )


class TestShellStateParsing(unittest.TestCase):
    def test_defaults(self) -> None:
        state = parse_shell_state(None)
        self.assertEqual(state.base, ShellBase.NONE)
        self.assertEqual(state.search_query, "")
        self.assertEqual(state.enabled_sources, frozenset())
        self.assertEqual(state.enabled_genres, frozenset())
        self.assertEqual(state.enabled_release_types, frozenset())
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

    def test_legacy_source_filter(self) -> None:
        restored = ShellState.from_dict({"source_filter": "qobuz"})
        self.assertEqual(restored.enabled_sources, frozenset({Source.QOBUZ}))

    def test_enabled_release_types_roundtrip(self) -> None:
        state = ShellState(
            enabled_release_types=frozenset({RELEASE_TYPE_FILTER_EP, RELEASE_TYPE_FILTER_SINGLE}),
        )
        restored = ShellState.from_dict(state.to_dict())
        self.assertEqual(restored.enabled_release_types, state.enabled_release_types)


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


class TestApplySourceFilter(unittest.TestCase):
    def test_all_sources(self) -> None:
        releases = [
            _release("local:1", Source.LOCAL),
            _release("tidal:1", Source.TIDAL),
        ]
        self.assertEqual(len(apply_source_filter(releases, frozenset())), 2)

    def test_single_source(self) -> None:
        releases = [
            _release("local:1", Source.LOCAL),
            _release("tidal:1", Source.TIDAL),
        ]
        filtered = apply_source_filter(releases, frozenset({Source.LOCAL}))
        self.assertEqual([r.id for r in filtered], ["local:1"])


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
