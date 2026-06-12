"""Tests for playback output profile computation."""

from __future__ import annotations

import unittest

from tunes_player.core.library.store import FileMetadata
from tunes_player.core.playback.output_profile import (
    HwAudioCaps,
    bit_depth_to_mpv_format,
    compute_output_profile,
)
from tunes_player.core.volume import pipewire_endpoint_id


class OutputProfileTests(unittest.TestCase):
    def test_bit_depth_to_mpv_format_uses_mpv_names(self) -> None:
        self.assertEqual(bit_depth_to_mpv_format(16), "s16")
        self.assertEqual(bit_depth_to_mpv_format(24), "s32")
        self.assertEqual(bit_depth_to_mpv_format(32), "s32")

    def test_direct_alsa_profile_audio_format(self) -> None:
        caps = HwAudioCaps(sample_rates=(48000,), bit_depths=(16, 24))
        profile, _path = compute_output_profile(
            file_meta=FileMetadata(
                path="/a.flac",
                codec="flac",
                duration_sec=1.0,
                sample_rate=48000,
                bit_depth=16,
                channels=2,
            ),
            hw_caps=caps,
            endpoint_id="alsa:hw:0:0",
            exclusive_enabled=False,
            device_volume=True,
            mpv_soft_volume=False,
        )
        self.assertEqual(profile.audio_format, "s16")

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

    def test_stream_metadata_bit_perfect_when_rate_matches(self) -> None:
        caps = HwAudioCaps(sample_rates=(44100, 48000, 96000), bit_depths=(16, 24))
        profile, path = compute_output_profile(
            file_meta=FileMetadata(
                path="",
                codec="flac",
                duration_sec=None,
                sample_rate=44100,
                bit_depth=24,
                channels=2,
            ),
            hw_caps=caps,
            endpoint_id="alsa:hw:0:0",
            exclusive_enabled=True,
            device_volume=True,
            mpv_soft_volume=False,
        )
        self.assertEqual(profile.target_rate, 44100)
        self.assertEqual(profile.audio_format, "s32")
        self.assertTrue(path.bit_perfect_playback)
        self.assertEqual(path.playback_note, "ALSA bit-perfect")

    def test_stream_metadata_resample_note_when_rate_unsupported(self) -> None:
        caps = HwAudioCaps(sample_rates=(48000, 96000), bit_depths=(16, 24))
        _profile, path = compute_output_profile(
            file_meta=FileMetadata(
                path="",
                codec="flac",
                duration_sec=None,
                sample_rate=44100,
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
        assert path.playback_note is not None
        self.assertIn("resampling", path.playback_note)

    def test_direct_alsa_bit_perfect_with_fixed_output_dac(self) -> None:
        caps = HwAudioCaps(sample_rates=(44100, 48000, 96000, 192000), bit_depths=(16, 24))
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
            endpoint_id="alsa:hw:1:0",
            exclusive_enabled=False,
            device_volume=False,
            mpv_soft_volume=False,
        )
        self.assertTrue(path.bit_perfect_playback)
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
