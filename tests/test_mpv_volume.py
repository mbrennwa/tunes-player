"""Tests for mpv software volume toggling."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from tunes_player.core.playback.output_profile import PlaybackOutputProfile
from tunes_player.engines.playback_client import MpvPlaybackClient


class MpvSoftwareVolumeTests(unittest.TestCase):
    def _engine(self, *, unity_gain: bool) -> MpvPlaybackClient:
        client = object.__new__(MpvPlaybackClient)
        client._unity_gain = unity_gain
        client._volume = 0.5
        client._use_device_output = False
        client._software_volume = not unity_gain
        client._keep_alsa_open_on_track_change = False
        client._last_output_format_key = None
        client.set_property = MagicMock()
        client.command = MagicMock(return_value={"error": "success"})
        return client

    def test_set_bit_perfect_false_enables_software_volume(self) -> None:
        engine = self._engine(unity_gain=True)
        self.assertFalse(engine._software_volume)

        engine.set_bit_perfect(False)
        engine.set_volume(0.25)

        self.assertTrue(engine._software_volume)
        engine.set_property.assert_any_call("volume", 25.0)

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

        engine.set_property.assert_any_call("volume", 50.0)


if __name__ == "__main__":
    unittest.main()
