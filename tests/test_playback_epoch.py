"""Tests for playback_epoch (seek bar reset on re-play)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tunes_player.core.backends.playable import PlayableSource
from tunes_player.core.config import ConfigManager
from tunes_player.core.models import Source, Track
from tunes_player.core.services import PlayerService


class _FakeEngine:
    def __init__(self) -> None:
        self._position_sec = 0.0
        self._duration_sec = 180.0
        self._playing = True
        self.loaded_uri: str | None = None

    def load(self, uri: str, *, start_sec: float = 0, output_profile: object = None) -> None:
        self.loaded_uri = uri
        self._position_sec = start_sec
        self._playing = True

    def set_output_profile(self, profile: object) -> None:
        pass

    def play(self) -> None:
        self._playing = True

    def pause(self) -> None:
        self._playing = False

    def stop(self) -> None:
        self.loaded_uri = None
        self._position_sec = 0.0
        self._playing = False

    def seek(self, position_sec: float) -> None:
        self._position_sec = max(0.0, position_sec)

    def set_volume(self, level: float) -> None:
        pass

    def set_bit_perfect(self, enabled: bool) -> None:
        pass

    def get_position(self) -> float:
        return self._position_sec

    def get_duration(self) -> float | None:
        return self._duration_sec

    def is_playing(self) -> bool:
        return self._playing

    def set_event_callback(self, callback: object) -> None:
        pass

    def quit(self) -> None:
        pass


class PlaybackEpochTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        data_dir = Path(self._tmpdir.name)
        config = ConfigManager(data_dir / "config.toml")
        config.load()
        self._service = PlayerService(config=config)
        self._service._engine = _FakeEngine()
        self._track = Track(
            id="local:track:1",
            title="Test Track",
            artist_name="Artist",
            album_title="Album",
            source=Source.LOCAL,
            duration_sec=180.0,
        )
        self._source = PlayableSource(
            uri="file:///tmp/test.flac",
            metadata=self._track,
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_replay_same_track_increments_epoch_and_resets_position(self) -> None:
        service = self._service
        track = self._track
        with (
            patch(
                "tunes_player.core.services.resolve_track",
                return_value=self._source,
            ),
            patch.object(service, "_record_playback"),
        ):
            service._start_queue_track(track)
            state1 = service.get_playback_state()
            self.assertEqual(state1.playback_epoch, 1)
            self.assertEqual(state1.position_sec, 0.0)
            self.assertIs(state1.current_track, track)

            engine = service._engine
            assert isinstance(engine, _FakeEngine)
            engine._position_sec = 42.0
            service._apply_engine_position(42.0)
            state_mid = service.get_playback_state()
            self.assertEqual(state_mid.position_sec, 42.0)

            service._start_queue_track(track)
            state2 = service.get_playback_state()
            self.assertEqual(state2.playback_epoch, 2)
            self.assertEqual(state2.position_sec, 0.0)
            self.assertEqual(state2.current_track, track)


if __name__ == "__main__":
    unittest.main()
