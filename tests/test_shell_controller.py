"""Shell controller fetch helpers."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from tunes_player.core.models import Release, ReleaseType, Source
from tunes_player.core.release_quality import QUALITY_FILTER_HI_RES
from tunes_player.core.shell_state import ShellBase, ShellState
from tunes_player.ui.gtk.shell_controller import (
    all_local_empty_message,
    empty_grid_message,
    fetch_base_releases,
    filter_empty_message,
    format_release_count_label,
)


def _release(release_id: str) -> Release:
    return Release(
        id=release_id,
        title="Album",
        artist_name="Artist",
        source=Source.LOCAL,
        track_count=1,
        release_type=ReleaseType.ALBUM,
    )


class TestFormatReleaseCountLabel(unittest.TestCase):
    def test_single_release(self) -> None:
        self.assertEqual(
            format_release_count_label(filtered_count=1),
            "1",
        )

    def test_multiple_releases(self) -> None:
        self.assertEqual(
            format_release_count_label(filtered_count=248),
            "248",
        )

    def test_filtered_subset(self) -> None:
        self.assertEqual(
            format_release_count_label(filtered_count=12, catalog_count=248),
            "12 of 248",
        )

    def test_matching_counts_omit_total(self) -> None:
        self.assertEqual(
            format_release_count_label(filtered_count=248, catalog_count=248),
            "248",
        )


class TestAllLocalEmptyMessage(unittest.TestCase):
    def test_without_folders(self) -> None:
        service = MagicMock()
        service.config.config.music_folders = []

        message = all_local_empty_message(service, has_unfiltered_releases=False)

        self.assertIn("Add folders", message)

    def test_with_folders_but_no_scan(self) -> None:
        service = MagicMock()
        service.config.config.music_folders = ["/music"]

        message = all_local_empty_message(service, has_unfiltered_releases=False)

        self.assertIn("scanned yet", message)
        self.assertIn("Watch folder", message)

    def test_with_indexed_releases_defers_to_filters(self) -> None:
        service = MagicMock()
        service.config.config.music_folders = ["/music"]

        message = all_local_empty_message(service, has_unfiltered_releases=True)

        self.assertIsNone(message)


class TestFilterEmptyMessage(unittest.TestCase):
    def test_quality_filter_message_lists_selected_tiers(self) -> None:
        state = ShellState(enabled_quality_tiers=frozenset({QUALITY_FILTER_HI_RES}))

        message = filter_empty_message(state)

        self.assertIsNotNone(message)
        assert message is not None
        self.assertIn("Hi-res", message)
        self.assertNotIn("Add folders", message)


class TestEmptyGridMessage(unittest.TestCase):
    def test_all_local_with_catalog_and_quality_filter(self) -> None:
        service = MagicMock()
        service.config.config.music_folders = ["/music"]
        state = ShellState(
            base=ShellBase.ALL_LOCAL,
            enabled_quality_tiers=frozenset({QUALITY_FILTER_HI_RES}),
        )

        message = empty_grid_message(service, state, catalog_count=3)

        self.assertIsNotNone(message)
        assert message is not None
        self.assertIn("Hi-res", message)
        self.assertNotIn("scanned yet", message)
        self.assertNotIn("Add folders", message)

    def test_all_local_without_catalog_still_prompts_scan(self) -> None:
        service = MagicMock()
        service.config.config.music_folders = ["/music"]
        state = ShellState(base=ShellBase.ALL_LOCAL)

        message = empty_grid_message(service, state, catalog_count=0)

        self.assertIsNotNone(message)
        assert message is not None
        self.assertIn("scanned yet", message)


class TestFetchBaseReleases(unittest.TestCase):
    def test_all_local_returns_list_releases(self) -> None:
        service = MagicMock()
        service.config.config.music_folders = ["/music"]
        expected = [_release("local:1"), _release("local:2")]
        service.list_releases.return_value = expected

        result = fetch_base_releases(service, ShellBase.ALL_LOCAL)

        service.list_releases.assert_called_once_with()
        self.assertEqual(result, expected)

    def test_all_local_without_folders_returns_empty(self) -> None:
        service = MagicMock()
        service.config.config.music_folders = []

        result = fetch_base_releases(service, ShellBase.ALL_LOCAL)

        service.list_releases.assert_not_called()
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
