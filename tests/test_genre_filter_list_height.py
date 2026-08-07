"""Tests for genre filter popover list height math."""

from __future__ import annotations

import unittest

from tunes_player.ui.gtk.searchable_check_filter import filter_list_max_height


class GenreFilterListMaxHeightTests(unittest.TestCase):
    def test_short_list_fits_without_scroll(self) -> None:
        self.assertEqual(
            filter_list_max_height(
                natural_list_height=100,
                available_height=392,
            ),
            100,
        )

    def test_long_list_capped_by_available_space(self) -> None:
        self.assertEqual(
            filter_list_max_height(
                natural_list_height=800,
                available_height=392,
            ),
            392,
        )

    def test_respects_min_list_height_on_tight_layout(self) -> None:
        self.assertEqual(
            filter_list_max_height(
                natural_list_height=800,
                available_height=80,
                min_list_height=120,
            ),
            120,
        )

    def test_returns_fallback_when_no_space(self) -> None:
        self.assertEqual(
            filter_list_max_height(
                natural_list_height=200,
                available_height=0,
                fallback_max=320,
            ),
            320,
        )

    def test_uses_full_available_space_without_artificial_ceiling(self) -> None:
        self.assertEqual(
            filter_list_max_height(
                natural_list_height=900,
                available_height=1072,
            ),
            900,
        )
