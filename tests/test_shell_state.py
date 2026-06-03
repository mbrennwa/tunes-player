"""Shell state persistence and source filtering."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tunes_player.core.config import ConfigManager
from tunes_player.core.models import Release, Source
from tunes_player.core.shell_state import (
    ShellBase,
    ShellState,
    apply_source_filter,
    parse_shell_state,
    release_from_cache_payload,
    release_to_cache_payload,
    releases_from_cache_payloads,
)


def _release(release_id: str, source: Source) -> Release:
    return Release(
        id=release_id,
        title="Title",
        artist_name="Artist",
        source=source,
        year=2024,
        genre="Rock",
    )


class TestShellStateParsing(unittest.TestCase):
    def test_defaults(self) -> None:
        state = parse_shell_state(None)
        self.assertEqual(state.base, ShellBase.NONE)
        self.assertEqual(state.search_query, "")
        self.assertEqual(state.enabled_sources, frozenset())
        self.assertEqual(state.cached_releases, ())

    def test_roundtrip_dict(self) -> None:
        state = ShellState(
            base=ShellBase.SEARCH,
            search_query="beatles",
            enabled_sources=frozenset({Source.TIDAL, Source.LOCAL}),
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


if __name__ == "__main__":
    unittest.main()
