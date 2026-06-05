"""Tests for viewport-visible album grid card index math."""

from __future__ import annotations

import unittest

from tunes_player.ui.gtk.album_grid import (
    ALBUM_GRID_SPACING,
    ALBUM_GRID_VIEW_MARGIN,
    album_grid_visible_card_indices,
)


class VisibleArtIndicesTests(unittest.TestCase):
    def test_empty_grid(self) -> None:
        self.assertEqual(
            album_grid_visible_card_indices(
                card_count=0,
                columns=3,
                tile_edge=140,
                scroll_y=0,
                viewport_height=600,
            ),
            (0, 0),
        )

    def test_top_of_list_with_prefetch(self) -> None:
        start, end = album_grid_visible_card_indices(
            card_count=10,
            columns=3,
            tile_edge=140,
            scroll_y=0,
            viewport_height=400,
            prefetch_rows=1,
        )
        self.assertEqual(start, 0)
        self.assertLessEqual(end, 10)
        self.assertGreater(end, 0)

    def test_mid_scroll(self) -> None:
        edge = 140
        stride = edge + ALBUM_GRID_SPACING
        margin = ALBUM_GRID_VIEW_MARGIN
        scroll_y = margin + stride

        start, end = album_grid_visible_card_indices(
            card_count=30,
            columns=3,
            tile_edge=edge,
            scroll_y=scroll_y,
            viewport_height=400,
            prefetch_rows=0,
        )
        self.assertGreaterEqual(start, 0)
        self.assertLess(end, 30)
        self.assertGreater(end, start)

    def test_bottom_clamps_to_card_count(self) -> None:
        start, end = album_grid_visible_card_indices(
            card_count=10,
            columns=3,
            tile_edge=140,
            scroll_y=10_000,
            viewport_height=400,
            prefetch_rows=1,
        )
        self.assertGreaterEqual(start, 0)
        self.assertEqual(end, 10)
        self.assertGreater(end, start)

    def test_prefetch_expands_range(self) -> None:
        without = album_grid_visible_card_indices(
            card_count=30,
            columns=3,
            tile_edge=140,
            scroll_y=0,
            viewport_height=300,
            prefetch_rows=0,
        )
        with_prefetch = album_grid_visible_card_indices(
            card_count=30,
            columns=3,
            tile_edge=140,
            scroll_y=0,
            viewport_height=300,
            prefetch_rows=1,
        )
        self.assertGreaterEqual(with_prefetch[1] - with_prefetch[0], without[1] - without[0])

    def test_partial_last_row(self) -> None:
        start, end = album_grid_visible_card_indices(
            card_count=8,
            columns=3,
            tile_edge=140,
            scroll_y=0,
            viewport_height=2000,
            prefetch_rows=0,
        )
        self.assertEqual(start, 0)
        self.assertEqual(end, 8)


if __name__ == "__main__":
    unittest.main()
