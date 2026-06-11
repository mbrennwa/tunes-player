"""Tests for release-level play/pause overlay behavior."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tunes_player.core.config import ConfigManager
from tunes_player.core.models import Source, Track
from tunes_player.core.services import PlayerService


class ReleasePlaybackToggleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        config = ConfigManager(Path(self._tmpdir.name) / "config.toml")
        config.load()
        self._service = PlayerService(config=config)
        self._track = Track(
            id="local:file:one",
            title="Track",
            artist_name="Artist",
            album_title="Album",
            source=Source.LOCAL,
        )
        self._release_id = "local:release:artist:album"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_is_release_playing_requires_active_playback(self) -> None:
        self._service._current_track = self._track
        self._service._current_release_id = self._release_id
        self._service._is_playing = True
        self.assertTrue(self._service.is_release_playing(self._release_id))
        self.assertFalse(self._service.is_release_playing("local:release:other"))

        self._service._is_playing = False
        self.assertFalse(self._service.is_release_playing(self._release_id))

        self._service._playback_load_active = True
        self.assertTrue(self._service.is_release_playing(self._release_id))
        self._service._playback_load_active = False

    def test_play_or_toggle_release_toggles_current_release(self) -> None:
        self._service._current_track = self._track
        self._service._current_release_id = self._release_id
        with patch.object(self._service, "toggle_play_pause") as toggle:
            with patch.object(self._service, "play_release") as play_release:
                self._service.play_or_toggle_release(self._release_id)
        toggle.assert_called_once_with()
        play_release.assert_not_called()

    def test_play_or_toggle_release_starts_other_release(self) -> None:
        self._service._current_track = self._track
        self._service._current_release_id = "local:release:other"
        with patch.object(self._service, "toggle_play_pause") as toggle:
            with patch.object(self._service, "play_release") as play_release:
                self._service.play_or_toggle_release(self._release_id, start_index=2)
        toggle.assert_not_called()
        play_release.assert_called_once_with(
            self._release_id,
            start_index=2,
        )


if __name__ == "__main__":
    unittest.main()
