"""Tests for sync vs async browse grid loading strategy."""

from __future__ import annotations

import unittest

from tunes_player.core.shell_state import ShellBase, ShellState
from tunes_player.ui.gtk.shell_controller import grid_load_is_sync


class GridLoadStrategyTests(unittest.TestCase):
    def test_none_preset_is_sync(self) -> None:
        state = ShellState(base=ShellBase.NONE)
        self.assertTrue(grid_load_is_sync(state, has_valid_cache=False))

    def test_valid_cache_is_sync(self) -> None:
        state = ShellState(base=ShellBase.NEW_MUSIC)
        self.assertTrue(grid_load_is_sync(state, has_valid_cache=True))

    def test_all_local_without_cache_is_sync(self) -> None:
        state = ShellState(base=ShellBase.ALL_LOCAL)
        self.assertTrue(grid_load_is_sync(state, has_valid_cache=False))

    def test_discover_without_cache_is_async(self) -> None:
        for base in (ShellBase.NEW_MUSIC, ShellBase.SUGGESTION):
            with self.subTest(base=base):
                state = ShellState(base=base)
                self.assertFalse(grid_load_is_sync(state, has_valid_cache=False))

    def test_search_without_cache_is_async(self) -> None:
        state = ShellState(base=ShellBase.SEARCH, search_query="artist")
        self.assertFalse(grid_load_is_sync(state, has_valid_cache=False))


if __name__ == "__main__":
    unittest.main()
