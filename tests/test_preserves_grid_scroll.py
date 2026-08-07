"""Grid scroll preserve policy for shell recreate reasons."""

from __future__ import annotations

import unittest

from tunes_player.ui.gtk.app import _preserves_grid_scroll


class PreservesGridScrollTests(unittest.TestCase):
    def test_preserves_library_growth_and_enrich(self) -> None:
        self.assertTrue(_preserves_grid_scroll("library_updated"))
        self.assertTrue(_preserves_grid_scroll("library_updated/all_local"))
        self.assertTrue(_preserves_grid_scroll("quality_enrich_visible_ids_changed"))

    def test_resets_on_sort_and_filter(self) -> None:
        self.assertFalse(_preserves_grid_scroll("sort_changed"))
        self.assertFalse(_preserves_grid_scroll("quality_filter"))
        self.assertFalse(_preserves_grid_scroll("source_filter"))
        self.assertFalse(_preserves_grid_scroll("shell_state_filters"))
        self.assertFalse(_preserves_grid_scroll("release_type_filter"))


if __name__ == "__main__":
    unittest.main()
