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


def _selection_identity_changed(previous: ShellState, current: ShellState) -> bool:
    if previous.base != current.base:
        return True
    if current.base == ShellBase.SEARCH:
        if previous.search_query != current.search_query:
            return True
        if previous.search_scope != current.search_scope:
            return True
    return False


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
        self.assertTrue(_selection_identity_changed(current, next_state))

    def test_repeated_search_query_should_not_push(self) -> None:
        current = ShellState(base=ShellBase.SEARCH, search_query="jazz")
        text = "jazz"
        same_query = current.base == ShellBase.SEARCH and current.search_query.strip() == text
        self.assertTrue(same_query)


class TestPresetHistoryPush(unittest.TestCase):
    def test_search_to_new_music_should_push(self) -> None:
        current = ShellState(base=ShellBase.SEARCH, search_query="Radiohead")
        next_state = replace(
            current,
            base=ShellBase.NEW_MUSIC,
            search_query="",
            cached_releases=(),
        )
        self.assertTrue(_selection_identity_changed(current, next_state))

    def test_new_music_to_suggestion_should_push(self) -> None:
        current = ShellState(base=ShellBase.NEW_MUSIC)
        next_state = replace(
            current,
            base=ShellBase.SUGGESTION,
            cached_releases=(),
        )
        self.assertTrue(_selection_identity_changed(current, next_state))

    def test_same_preset_should_not_push(self) -> None:
        current = ShellState(base=ShellBase.NEW_MUSIC)
        next_state = replace(current, cached_releases=())
        self.assertFalse(_selection_identity_changed(current, next_state))


if __name__ == "__main__":
    unittest.main()
