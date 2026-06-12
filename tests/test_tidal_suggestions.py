"""Tests for TIDAL suggestion discovery helpers."""

from __future__ import annotations

import unittest

from tunes_player.core.backends.tidal.client import (
    _category_is_new_release_rail,
    _category_is_recommendation_rail,
    _collect_tidal_page_objects,
)


class _FakeCategory:
    def __init__(self, *, title: str = "", mix_type: object | None = None) -> None:
        self.title = title
        self.mix_type = mix_type
        self.items = []


class _FakeMixType:
    def __init__(self, name: str) -> None:
        self.name = name


class TestCategoryIsRecommendationRail(unittest.TestCase):
    def test_matches_for_you(self) -> None:
        self.assertTrue(_category_is_recommendation_rail(_FakeCategory(title="For You")))

    def test_excludes_new_releases(self) -> None:
        self.assertFalse(
            _category_is_recommendation_rail(_FakeCategory(title="New releases")),
        )
        self.assertTrue(_category_is_new_release_rail(_FakeCategory(title="New releases")))

    def test_mix_type_not_new_release(self) -> None:
        category = _FakeCategory(
            title="Your mix",
            mix_type=_FakeMixType("DAILY_MIX"),
        )
        self.assertTrue(_category_is_recommendation_rail(category))

    def test_new_release_mix_type_excluded(self) -> None:
        category = _FakeCategory(
            title="Fresh",
            mix_type=_FakeMixType("NEW_RELEASE_MIX"),
        )
        self.assertFalse(_category_is_recommendation_rail(category))


class _FakePage:
    def __init__(self, categories: list[object]) -> None:
        self.categories = categories


class _FakeItem:
    pass


class TestTidalRailFilter(unittest.TestCase):
    def test_new_releases_filter_skips_recommendation_categories(self) -> None:
        new_category = _FakeCategory(title="New releases")
        new_category.items = [_FakeItem()]
        rec_category = _FakeCategory(title="For You")
        rec_category.items = [_FakeItem()]
        page = _FakePage([new_category, rec_category])
        collected = _collect_tidal_page_objects(
            session=object(),
            page=page,
            visited=set(),
            rail_filter="new_releases",
        )
        self.assertEqual(len(collected), 1)
        self.assertTrue(collected[0].from_new_release_rail)

    def test_recommendations_filter_skips_new_release_categories(self) -> None:
        new_category = _FakeCategory(title="New releases")
        new_category.items = [_FakeItem()]
        rec_category = _FakeCategory(title="For You")
        rec_category.items = [_FakeItem()]
        page = _FakePage([new_category, rec_category])
        collected = _collect_tidal_page_objects(
            session=object(),
            page=page,
            visited=set(),
            rail_filter="recommendations",
        )
        self.assertEqual(len(collected), 1)
        self.assertTrue(collected[0].from_recommendation_rail)


if __name__ == "__main__":
    unittest.main()
