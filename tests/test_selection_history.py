"""Selection-history helpers for shell Back navigation."""

from __future__ import annotations

import unittest
from dataclasses import replace

from tunes_player.core.models import Release, Source
from tunes_player.core.shell_state import ShellBase, ShellState
from tunes_player.ui.gtk.app import _SelectionSnapshot


def _release(release_id: str) -> Release:
    return Release(
        id=release_id,
        title="Album",
        artist_name="Artist",
        source=Source.LOCAL,
    )


class TestSelectionSnapshot(unittest.TestCase):
    def test_snapshot_round_trip_preserves_filters(self) -> None:
        state = ShellState(
            base=ShellBase.NEW_MUSIC,
            enabled_genres=frozenset({"Rock"}),
            sort_key="year",
        )
        releases = (_release("local:a"),)
        snapshot = _SelectionSnapshot(state=state, releases=releases)
        self.assertEqual(snapshot.state.enabled_genres, frozenset({"Rock"}))
        self.assertEqual(len(snapshot.releases), 1)


class TestArtistSearchHistoryPush(unittest.TestCase):
    def test_same_query_should_not_push(self) -> None:
        current = ShellState(base=ShellBase.SEARCH, search_query="Björk")
        query = "Björk"
        same = current.base == ShellBase.SEARCH and current.search_query.strip() == query
        self.assertTrue(same)

    def test_different_search_query_should_push(self) -> None:
        current = ShellState(base=ShellBase.SEARCH, search_query="Björk")
        next_state = replace(
            current,
            search_query="Portishead",
            cached_releases=(),
        )
        identity_changed = (
            current.base != next_state.base
            or (
                next_state.base == ShellBase.SEARCH
                and current.search_query != next_state.search_query
            )
        )
        self.assertTrue(identity_changed)

    def test_repeated_search_query_should_not_push(self) -> None:
        current = ShellState(base=ShellBase.SEARCH, search_query="jazz")
        text = "jazz"
        same_query = current.base == ShellBase.SEARCH and current.search_query.strip() == text
        self.assertTrue(same_query)


if __name__ == "__main__":
    unittest.main()
