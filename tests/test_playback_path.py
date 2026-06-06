"""Tests for playback path derivation from negotiated mpv state."""

from __future__ import annotations

import unittest

from tunes_player.core.library.store import FileMetadata
from tunes_player.core.playback.output_profile import PlaybackOutputProfile
from tunes_player.core.playback.playback_path import (
    NegotiatedPlaybackState,
    derive_playback_path_info,
)
from tunes_player.core.volume import pipewire_endpoint_id


class DerivePlaybackPathTests(unittest.TestCase):
    def _profile(self, **kwargs: object) -> PlaybackOutputProfile:
        defaults = {
            "direct_alsa": True,
            "use_exclusive": True,
            "allow_resample": False,
            "target_rate": 96000,
            "target_bit_depth": 24,
            "target_channels": 2,
            "audio_format": "s32",
        }
        defaults.update(kwargs)
        return PlaybackOutputProfile(**defaults)  # type: ignore[arg-type]

    def test_pipewire_endpoint_not_bit_perfect(self) -> None:
        path = derive_playback_path_info(
            file_meta=None,
            profile=PlaybackOutputProfile(
                direct_alsa=False,
                use_exclusive=False,
                allow_resample=True,
            ),
            negotiated=NegotiatedPlaybackState(),
            endpoint_id=pipewire_endpoint_id("alsa_output.pci.analog-stereo"),
            device_volume=True,
            mpv_soft_volume=False,
        )
        self.assertFalse(path.bit_perfect_playback)
        self.assertEqual(path.playback_note, "via PipeWire")

    def test_direct_alsa_bit_perfect_from_negotiated_state(self) -> None:
        path = derive_playback_path_info(
            file_meta=FileMetadata(
                path="/a.flac",
                codec="flac",
                duration_sec=1.0,
                sample_rate=96000,
                bit_depth=24,
                channels=2,
            ),
            profile=self._profile(),
            negotiated=NegotiatedPlaybackState(
                ao="alsa",
                audio_samplerate=96000,
                audio_format="s32",
                alsa_resample=False,
            ),
            endpoint_id="alsa:hw:0:0",
            device_volume=True,
            mpv_soft_volume=False,
        )
        self.assertTrue(path.bit_perfect_playback)
        self.assertEqual(path.playback_note, "ALSA bit-perfect")

    def test_resample_note_when_negotiated_rate_differs(self) -> None:
        path = derive_playback_path_info(
            file_meta=FileMetadata(
                path="/a.flac",
                codec="flac",
                duration_sec=1.0,
                sample_rate=192000,
                bit_depth=24,
                channels=2,
            ),
            profile=self._profile(
                allow_resample=True,
                target_rate=96000,
            ),
            negotiated=NegotiatedPlaybackState(
                audio_samplerate=96000,
                audio_format="s32",
                alsa_resample=True,
            ),
            endpoint_id="alsa:hw:0:0",
            device_volume=True,
            mpv_soft_volume=False,
        )
        self.assertFalse(path.bit_perfect_playback)
        assert path.playback_note is not None
        self.assertIn("resampling", path.playback_note)


if __name__ == "__main__":
    unittest.main()
