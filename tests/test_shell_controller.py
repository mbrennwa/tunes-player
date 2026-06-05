"""Shell controller fetch helpers."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from tunes_player.core.models import Release, ReleaseType, Source
from tunes_player.core.shell_state import ShellBase
from tunes_player.ui.gtk.shell_controller import fetch_base_releases


def _release(release_id: str) -> Release:
    return Release(
        id=release_id,
        title="Album",
        artist_name="Artist",
        source=Source.LOCAL,
        track_count=1,
        release_type=ReleaseType.ALBUM,
    )


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
