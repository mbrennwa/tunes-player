"""Tests for PlayerService mute/unmute."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tunes_player.core.config import ConfigManager
from tunes_player.core.services import PlayerService


class _VolumeEngine:
    def __init__(self) -> None:
        self.level = 1.0

    def set_volume(self, level: float) -> None:
        self.level = level

    def set_bit_perfect(self, enabled: bool) -> None:
        pass

    def get_position(self) -> float:
        return 0.0

    def get_duration(self) -> float | None:
        return None

    def is_playing(self) -> bool:
        return False

    def set_event_callback(self, callback: object) -> None:
        pass

    def load(self, uri: str, *, start_sec: float = 0) -> None:
        pass

    def play(self) -> None:
        pass

    def pause(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def seek(self, position_sec: float) -> None:
        pass

    def quit(self) -> None:
        pass


class MuteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        config = ConfigManager(Path(self._tmpdir.name) / "config.toml")
        config.load()
        self._service = PlayerService(config=config)
        self._engine = _VolumeEngine()
        self._service._engine = self._engine
        self._service.set_volume(0.5, notify=False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_toggle_mute_zeros_output_but_keeps_level(self) -> None:
        self._service.toggle_mute()
        state = self._service.get_playback_state()
        self.assertTrue(state.muted)
        self.assertEqual(state.volume, 0.5)
        self.assertEqual(self._engine.level, 0.0)

        self._service.toggle_mute()
        state = self._service.get_playback_state()
        self.assertFalse(state.muted)
        self.assertEqual(self._engine.level, 0.5)

    def test_set_volume_unmutes(self) -> None:
        self._service.toggle_mute()
        self._service.set_volume(0.4, notify=False)
        state = self._service.get_playback_state()
        self.assertFalse(state.muted)
        self.assertEqual(self._engine.level, 0.4)


if __name__ == "__main__":
    unittest.main()
