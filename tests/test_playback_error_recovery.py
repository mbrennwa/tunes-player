"""Playback should recover after a prior source failure."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tunes_player.core.config import ConfigManager
from tunes_player.core.models import Source, Track
from tunes_player.core.services import PlayerService


class PlaybackErrorRecoveryTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_stale_qobuz_error_does_not_block_next_play_attempt(self) -> None:
        self._service._engine_error = (
            "This track isn't available for streaming on Qobuz."
        )

        def resolve_track(*_args: object, **_kwargs: object) -> None:
            self.assertIsNone(self._service._engine_error)
            return None

        with patch(
            "tunes_player.core.services.resolve_track",
            side_effect=resolve_track,
        ):
            self._service._start_queue_track(self._track)

    def test_ensure_engine_not_blocked_by_stale_playback_error(self) -> None:
        self._service._engine_error = (
            "This track isn't available for streaming on Qobuz."
        )
        existing = object()
        self._service._engine = existing
        self.assertIs(self._service._ensure_engine(), existing)


if __name__ == "__main__":
    unittest.main()
