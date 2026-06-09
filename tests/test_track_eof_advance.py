"""Queue advance on demuxer EOF (#44 / in-process #46)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from tunes_player.core.config import ConfigManager
from tunes_player.core.models import Source, Track
from tunes_player.core.services import PlayerService


class _EofEngine:
    def __init__(self, *, duration: float = 240.0, position: float = 239.8) -> None:
        self._duration_sec = duration
        self._position_sec = position
        self._time_pos_sec = position
        self._playing = False

    def get_position(self) -> float:
        return self._position_sec

    def query_time_pos(self) -> float:
        return self._time_pos_sec

    def get_duration(self) -> float | None:
        return self._duration_sec

    def is_playing(self) -> bool:
        return self._playing


class TrackEofAdvanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        config = ConfigManager(Path(self._tmpdir.name) / "config.toml")
        self._service = PlayerService(config=config)

    def tearDown(self) -> None:
        self._service._engine = None
        self._service.shutdown()
        self._tmpdir.cleanup()

    def _track(self, track_id: str) -> Track:
        return Track(
            id=track_id,
            title="Track",
            artist_name="Artist",
            album_title="Album",
            source=Source.LOCAL,
            duration_sec=240.0,
        )

    def test_track_eof_event_advances_queue(self) -> None:
        self._service._playlist_meta = [
            self._track("local:/a.flac"),
            self._track("local:/b.flac"),
        ]
        self._service._queue_index = 0
        self._service._current_track = self._service._playlist_meta[0]
        self._service._playback_intended = True
        self._service._is_playing = True
        self._service._duration_sec = None
        self._service._position_sec = 120.0
        self._service._engine = _EofEngine()
        self._service._play_queue_index = MagicMock()

        self._service._handle_engine_event("track_eof")

        self._service._play_queue_index.assert_called_once_with(1)

    def test_poll_playback_checks_advance_near_end(self) -> None:
        self._service._playlist_meta = [
            self._track("local:/a.flac"),
            self._track("local:/b.flac"),
        ]
        self._service._queue_index = 0
        self._service._playback_intended = True
        self._service._is_playing = False
        self._service._duration_sec = 240.0
        self._service._position_sec = 239.5
        self._service._engine = _EofEngine(position=239.5)
        self._service._play_queue_index = MagicMock()

        self._service.poll_playback()

        self._service._play_queue_index.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
