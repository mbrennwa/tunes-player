"""PlayerService release label integration tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from tunes_player.core.config import ConfigManager
from tunes_player.core.models import Release, ReleaseType, Source
from tunes_player.core.services import PlayerService


def _local_release(release_id: str) -> Release:
    return Release(
        id=release_id,
        title="Album",
        artist_name="Artist",
        source=Source.LOCAL,
        track_count=1,
        release_type=ReleaseType.ALBUM,
    )


class PlayerServiceLabelledReleasesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        config = ConfigManager(Path(self._tmp.name) / "config.json")
        config.load()
        self._service = PlayerService(config=config)

    def tearDown(self) -> None:
        self._service.shutdown()
        self._tmp.cleanup()

    def test_list_labelled_releases_resolves_local_ids(self) -> None:
        release_id = "local:album:test"
        self._service._store.list_labelled_release_ids = MagicMock(
            return_value=(release_id,),
        )
        self._service.get_release_labels = MagicMock(return_value=frozenset({"buy"}))
        self._service.get_release_summary = MagicMock(
            return_value=_local_release(release_id),
        )

        labelled = self._service.list_labelled_releases()

        self.assertEqual([release.id for release in labelled], [release_id])
        self._service.get_release_summary.assert_called_once_with(release_id)

    def test_list_labelled_releases_skips_missing_release(self) -> None:
        self._service._store.list_labelled_release_ids = MagicMock(
            return_value=("tidal:missing",),
        )
        self._service.get_release_labels = MagicMock(return_value=frozenset({"buy"}))
        self._service.get_release_summary = MagicMock(return_value=None)

        self.assertEqual(self._service.list_labelled_releases(), [])
        releases, unavailable = self._service.list_labelled_browse()
        self.assertEqual(releases, [])
        self.assertEqual(unavailable, ("tidal:missing",))

    def test_list_labelled_releases_skips_release_that_raises(self) -> None:
        ok_id = "local:album:ok"
        self._service._store.list_labelled_release_ids = MagicMock(
            return_value=("tidal:album:99", ok_id),
        )
        self._service.get_release_labels = MagicMock(return_value=frozenset({"buy"}))
        self._service.get_release_summary = MagicMock(
            side_effect=[RuntimeError("gone"), _local_release(ok_id)],
        )

        releases, unavailable = self._service.list_labelled_browse()

        self.assertEqual([release.id for release in releases], [ok_id])
        self.assertEqual(unavailable, ("tidal:album:99",))

    def test_toggle_normalizes_quality_tile_id(self) -> None:
        self._service.toggle_release_label("tidal:album:99@cd", "buy", on=True)
        self.assertEqual(
            self._service.get_release_labels("tidal:album:99@hi_res"),
            frozenset({"buy"}),
        )
        self.assertEqual(
            self._service.get_release_labels("tidal:album:99"),
            frozenset({"buy"}),
        )


if __name__ == "__main__":
    unittest.main()
