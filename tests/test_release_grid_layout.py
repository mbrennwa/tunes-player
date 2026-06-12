"""Tests for responsive release grid layout math."""

from __future__ import annotations

import unittest

from tunes_player.ui.gtk.release_grid import (
    RELEASE_GRID_VIEW_MARGIN,
    RELEASE_TILE_MAX_EDGE,
    RELEASE_TILE_MIN_EDGE,
    release_grid_content_inner_width,
    release_grid_inner_width,
    release_grid_layout,
    release_grid_min_content_width,
    release_grid_resolve_inner_width,
)


class ReleaseGridLayoutTests(unittest.TestCase):
    def test_min_content_width(self) -> None:
        self.assertEqual(
            release_grid_min_content_width(),
            2 * RELEASE_GRID_VIEW_MARGIN + RELEASE_TILE_MIN_EDGE,
        )

    def test_inner_width_from_window(self) -> None:
        self.assertEqual(
            release_grid_inner_width(800, sidebar_width=180, horizontal_padding=36),
            584,
        )
        self.assertEqual(release_grid_inner_width(100, sidebar_width=180, horizontal_padding=36), 0)

    def test_inner_width_from_parent_outer_width(self) -> None:
        self.assertEqual(
            release_grid_content_inner_width(1000, margin_start=18, margin_end=18),
            964,
        )
        self.assertEqual(release_grid_content_inner_width(0), 0)

    def test_resolve_inner_width_growing(self) -> None:
        inner, last_vp, last_win = release_grid_resolve_inner_width(
            viewport_inner=520,
            window_inner=964,
            last_viewport_inner=520,
            last_window_inner=520,
        )
        self.assertEqual(inner, 964)
        self.assertEqual((last_vp, last_win), (520, 964))

    def test_resolve_inner_width_shrinking(self) -> None:
        inner, last_vp, last_win = release_grid_resolve_inner_width(
            viewport_inner=484,
            window_inner=960,
            last_viewport_inner=964,
            last_window_inner=960,
        )
        self.assertEqual(inner, 484)
        self.assertEqual((last_vp, last_win), (484, 960))

    def test_narrow_single_column_at_min(self) -> None:
        columns, edge = release_grid_layout(100)
        self.assertEqual((columns, edge), (1, RELEASE_TILE_MIN_EDGE))

    def test_exactly_min_width(self) -> None:
        columns, edge = release_grid_layout(RELEASE_TILE_MIN_EDGE)
        self.assertEqual((columns, edge), (1, RELEASE_TILE_MIN_EDGE))

    def test_two_columns(self) -> None:
        inner = 2 * RELEASE_TILE_MIN_EDGE + 12
        columns, edge = release_grid_layout(inner)
        self.assertEqual(columns, 2)
        self.assertEqual(edge, RELEASE_TILE_MIN_EDGE)

    def test_clamps_to_max_tile(self) -> None:
        inner = 3 * RELEASE_TILE_MAX_EDGE + 2 * 12
        columns, edge = release_grid_layout(inner)
        self.assertEqual(columns, 3)
        self.assertEqual(edge, RELEASE_TILE_MAX_EDGE)

    def test_wide_adds_columns(self) -> None:
        inner = 900
        columns, edge = release_grid_layout(inner)
        self.assertGreaterEqual(columns, 4)
        self.assertGreaterEqual(edge, RELEASE_TILE_MIN_EDGE)
        self.assertLessEqual(edge, RELEASE_TILE_MAX_EDGE)
        used = columns * edge + (columns - 1) * 12
        self.assertLessEqual(used, inner)


if __name__ == "__main__":
    unittest.main()
