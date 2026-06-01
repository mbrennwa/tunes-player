"""Tests for responsive album grid layout math."""

from __future__ import annotations

import unittest

from tunes_player.ui.gtk.album_grid import (
    ALBUM_GRID_VIEW_MARGIN,
    ALBUM_TILE_MAX_EDGE,
    ALBUM_TILE_MIN_EDGE,
    album_grid_content_inner_width,
    album_grid_inner_width,
    album_grid_layout,
    album_grid_min_content_width,
)


class AlbumGridLayoutTests(unittest.TestCase):
    def test_min_content_width(self) -> None:
        self.assertEqual(
            album_grid_min_content_width(),
            2 * ALBUM_GRID_VIEW_MARGIN + ALBUM_TILE_MIN_EDGE,
        )

    def test_inner_width_from_window(self) -> None:
        self.assertEqual(
            album_grid_inner_width(800, sidebar_width=180, horizontal_padding=36),
            584,
        )
        self.assertEqual(album_grid_inner_width(100, sidebar_width=180, horizontal_padding=36), 0)

    def test_inner_width_from_allocation(self) -> None:
        self.assertEqual(
            album_grid_content_inner_width(500, margin_start=18, margin_end=18),
            464,
        )
        self.assertEqual(album_grid_content_inner_width(0), 0)

    def test_narrow_single_column_at_min(self) -> None:
        columns, edge = album_grid_layout(100)
        self.assertEqual((columns, edge), (1, ALBUM_TILE_MIN_EDGE))

    def test_exactly_min_width(self) -> None:
        columns, edge = album_grid_layout(ALBUM_TILE_MIN_EDGE)
        self.assertEqual((columns, edge), (1, ALBUM_TILE_MIN_EDGE))

    def test_two_columns(self) -> None:
        inner = 2 * ALBUM_TILE_MIN_EDGE + 12
        columns, edge = album_grid_layout(inner)
        self.assertEqual(columns, 2)
        self.assertEqual(edge, ALBUM_TILE_MIN_EDGE)

    def test_clamps_to_max_tile(self) -> None:
        inner = 3 * ALBUM_TILE_MAX_EDGE + 2 * 12
        columns, edge = album_grid_layout(inner)
        self.assertEqual(columns, 3)
        self.assertEqual(edge, ALBUM_TILE_MAX_EDGE)

    def test_wide_adds_columns(self) -> None:
        inner = 900
        columns, edge = album_grid_layout(inner)
        self.assertGreaterEqual(columns, 4)
        self.assertGreaterEqual(edge, ALBUM_TILE_MIN_EDGE)
        self.assertLessEqual(edge, ALBUM_TILE_MAX_EDGE)
        used = columns * edge + (columns - 1) * 12
        self.assertLessEqual(used, inner)


if __name__ == "__main__":
    unittest.main()
