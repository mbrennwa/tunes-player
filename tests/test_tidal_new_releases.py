"""Tests for TIDAL new-release discovery helpers."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from tunes_player.core.backends.tidal.client import (
    _category_is_new_release_rail,
    _should_expand_tidal_category,
    _tidal_album_added_ns,
    _tidal_album_within_cutoff,
)


class _FakeMore:
    api_path = "/more"


class _FakeCategory:
    def __init__(
        self,
        *,
        title: str = "",
        items: list[object] | None = None,
        more: object | None = None,
    ) -> None:
        self.title = title
        self.items = items or []
        self._more = more


class TestTidalAlbumAddedNs(unittest.TestCase):
    def test_prefers_tidal_stream_start_over_original_release(self) -> None:
        album = type(
            "Album",
            (),
            {
                "release_date": datetime(1999, 1, 1, tzinfo=timezone.utc),
                "tidal_release_date": datetime(2026, 5, 1, tzinfo=timezone.utc),
                "user_date_added": None,
            },
        )()
        added_ns = _tidal_album_added_ns(album)
        expected = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        self.assertEqual(added_ns, expected)


class TestTidalAlbumWithinCutoff(unittest.TestCase):
    def test_keeps_album_without_dates(self) -> None:
        album = type("Album", (), {})()
        self.assertTrue(_tidal_album_within_cutoff(album, 0))

    def test_filters_old_stream_start(self) -> None:
        album = type(
            "Album",
            (),
            {
                "tidal_release_date": datetime(2020, 1, 1, tzinfo=timezone.utc),
                "user_date_added": None,
                "release_date": None,
            },
        )()
        cutoff = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        self.assertFalse(_tidal_album_within_cutoff(album, cutoff))


class TestCategoryIsNewReleaseRail(unittest.TestCase):
    def test_matches_english_title(self) -> None:
        self.assertTrue(_category_is_new_release_rail(_FakeCategory(title="New releases")))

    def test_matches_german_title(self) -> None:
        self.assertTrue(_category_is_new_release_rail(_FakeCategory(title="Neu für dich")))


class TestShouldExpandTidalCategory(unittest.TestCase):
    def test_expands_when_title_hints_new_releases(self) -> None:
        category = _FakeCategory(title="New releases", more=_FakeMore())
        self.assertTrue(_should_expand_tidal_category(category, new_release_rail=False))

    def test_expands_when_parent_rail_even_without_title(self) -> None:
        category = _FakeCategory(title="Albums", more=_FakeMore())
        self.assertTrue(
            _should_expand_tidal_category(category, new_release_rail=True),
        )

    def test_skips_without_more_link(self) -> None:
        category = _FakeCategory(title="New releases")
        self.assertFalse(_should_expand_tidal_category(category, new_release_rail=False))

if __name__ == "__main__":
    unittest.main()
