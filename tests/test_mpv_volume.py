"""Tests for mpv software volume toggling."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from tunes_player.core.playback.output_profile import PlaybackOutputProfile
from tunes_player.engines.mpv import MpvEngine


class MpvSoftwareVolumeTests(unittest.TestCase):
    def _engine(self, *, unity_gain: bool) -> MpvEngine:
        player = MagicMock()
        player.volume = 100
        with patch("mpv.MPV", return_value=player):
            engine = MpvEngine(
                unity_gain=unity_gain,
                volume=0.5,
                use_device_output=False,
            )
        engine._player = player
        return engine

    def test_set_bit_perfect_false_enables_software_volume(self) -> None:
        engine = self._engine(unity_gain=True)
        self.assertFalse(engine._software_volume)

        engine.set_bit_perfect(False)
        engine.set_volume(0.25)

        self.assertTrue(engine._software_volume)
        self.assertEqual(engine._player.volume, 25.0)

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

        engine._apply_track_format(profile)

        self.assertEqual(engine._player.volume, 50.0)


if __name__ == "__main__":
    unittest.main()
