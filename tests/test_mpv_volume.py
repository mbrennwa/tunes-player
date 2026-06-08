"""Tests for mpv software volume toggling."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from tunes_player.core.playback.output_profile import PlaybackOutputProfile
from tunes_player.engines.mpv import MpvEngine


class MpvSoftwareVolumeTests(unittest.TestCase):
    def _engine(self, *, unity_gain: bool) -> MpvEngine:
        engine = object.__new__(MpvEngine)
        engine._unity_gain = unity_gain
        engine._volume = 0.5
        engine._use_device_output = False
        engine._software_volume = not unity_gain
        engine._set_property = MagicMock()
        engine._apply_software_volume = MagicMock()
        return engine

    def test_set_bit_perfect_false_enables_software_volume(self) -> None:
        engine = self._engine(unity_gain=True)
        self.assertFalse(engine._software_volume)

        engine.set_bit_perfect(False)
        engine.set_volume(0.25)

        self.assertTrue(engine._software_volume)
        engine._apply_software_volume.assert_called()

    def test_direct_alsa_track_load_keeps_software_volume(self) -> None:
        engine = self._engine(unity_gain=False)
        profile = PlaybackOutputProfile(
            direct_alsa=True,
            use_exclusive=False,
            allow_resample=False,
            target_rate=None,
            audio_format=None,
            target_channels=None,
        )

        engine._keep_alsa_open_on_track_change = False
        engine._last_output_format_key = None
        engine._apply_track_format = MpvEngine._apply_track_format.__get__(engine)
        engine._set_property = MagicMock()
        engine._apply_software_volume = MagicMock()

        engine._apply_track_format(profile)

        engine._apply_software_volume.assert_called()


if __name__ == "__main__":
    unittest.main()
