"""Tests for playback output profile computation."""

from __future__ import annotations

import unittest

from tunes_player.core.library.store import FileMetadata
from tunes_player.core.playback.output_profile import (
    HwAudioCaps,
    compute_output_profile,
)
from tunes_player.core.volume import pipewire_endpoint_id


class OutputProfileTests(unittest.TestCase):
    def test_pipewire_sink_not_bit_perfect(self) -> None:
        _profile, path = compute_output_profile(
            file_meta=FileMetadata(
                path="/a.flac",
                codec="flac",
                duration_sec=1.0,
                sample_rate=96000,
                bit_depth=24,
                channels=2,
            ),
            hw_caps=None,
            endpoint_id=pipewire_endpoint_id("alsa_output.pci.analog-stereo"),
            exclusive_enabled=False,
            device_volume=True,
            mpv_soft_volume=False,
        )
        self.assertFalse(path.bit_perfect_playback)
        self.assertEqual(path.playback_note, "via PipeWire")

    def test_direct_alsa_bit_perfect_when_caps_match(self) -> None:
        caps = HwAudioCaps(sample_rates=(44100, 96000), bit_depths=(16, 24))
        _profile, path = compute_output_profile(
            file_meta=FileMetadata(
                path="/a.flac",
                codec="flac",
                duration_sec=1.0,
                sample_rate=96000,
                bit_depth=24,
                channels=2,
            ),
            hw_caps=caps,
            endpoint_id="alsa:hw:0:0",
            exclusive_enabled=True,
            device_volume=True,
            mpv_soft_volume=False,
        )
        self.assertTrue(path.bit_perfect_playback)
        self.assertEqual(path.playback_note, "ALSA bit-perfect")

    def test_resample_note_when_rate_unsupported(self) -> None:
        caps = HwAudioCaps(sample_rates=(44100, 48000, 96000), bit_depths=(16, 24))
        _profile, path = compute_output_profile(
            file_meta=FileMetadata(
                path="/a.flac",
                codec="flac",
                duration_sec=1.0,
                sample_rate=192000,
                bit_depth=24,
                channels=2,
            ),
            hw_caps=caps,
            endpoint_id="alsa:hw:0:0",
            exclusive_enabled=False,
            device_volume=True,
            mpv_soft_volume=False,
        )
        self.assertFalse(path.bit_perfect_playback)
        self.assertIsNotNone(path.playback_note)
        assert path.playback_note is not None
        self.assertEqual(
            path.playback_note,
            "ALSA 192 kHz → 96 kHz resampling",
        )


    def test_alsa_note_when_streaming_without_file_metadata(self) -> None:
        _profile, path = compute_output_profile(
            file_meta=None,
            hw_caps=None,
            endpoint_id="alsa:hw:0:0",
            exclusive_enabled=False,
            device_volume=True,
            mpv_soft_volume=False,
        )
        self.assertEqual(path.playback_note, "ALSA bit-perfect")

    def test_pipewire_note_when_streaming(self) -> None:
        _profile, path = compute_output_profile(
            file_meta=None,
            hw_caps=None,
            endpoint_id=pipewire_endpoint_id("alsa_output.pci.analog-stereo"),
            exclusive_enabled=False,
            device_volume=True,
            mpv_soft_volume=False,
        )
        self.assertEqual(path.playback_note, "via PipeWire")


if __name__ == "__main__":
    unittest.main()
