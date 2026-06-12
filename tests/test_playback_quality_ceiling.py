"""Tests for shell quality filter playback preference snapshot (#50, #54)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tunes_player.core.config import ConfigManager
from tunes_player.core.models import Source, Track
from tunes_player.core.release_quality import (
    QUALITY_FILTER_CD,
    PlaybackPreference,
)
from tunes_player.core.services import PlayerService


class PlaybackPreferenceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        config = ConfigManager(Path(self._tmp.name) / "config.json")
        config.load()
        config.update_shell_quality_tiers(frozenset({QUALITY_FILTER_CD}))
        self._service = PlayerService(config=config)
        self._track = Track(
            id="qobuz:track:1",
            title="Song",
            artist_name="Artist",
            release_title="Album",
            source=Source.QOBUZ,
        )
        self._cd_preference = PlaybackPreference(max_tier=QUALITY_FILTER_CD)

    def tearDown(self) -> None:
        self._service.shutdown()
        self._tmp.cleanup()

    def test_start_playlist_snapshots_preference_for_queue(self) -> None:
        self._service._start_playlist(
            [self._track],
            playback_preference=self._cd_preference,
        )
        self.assertEqual(
            self._service._playlist_playback_preference,
            self._cd_preference,
        )

    def test_resume_playlist_keeps_existing_preference(self) -> None:
        self._service._playlist_playback_preference = self._cd_preference
        self._service._playlist_meta = [self._track]
        self._service.config.update_shell_quality_tiers(frozenset())
        self._service._start_playlist([self._track])
        self.assertEqual(
            self._service._playlist_playback_preference,
            self._cd_preference,
        )

    def test_build_prepared_track_load_passes_queue_preference(self) -> None:
        self._service._playlist_playback_preference = self._cd_preference
        with patch(
            "tunes_player.core.services.resolve_track",
            return_value=MagicMock(format_label="16-bit / 44.1 kHz"),
        ) as resolve:
            prepared = self._service._build_prepared_track_load(
                self._track,
                resume=True,
                generation=1,
            )
        self.assertIsNone(prepared.error)
        resolve.assert_called_once()
        self.assertEqual(
            resolve.call_args.kwargs["playback_preference"],
            self._cd_preference,
        )


if __name__ == "__main__":
    unittest.main()
