"""Tests for playback format labels."""

from __future__ import annotations

import unittest

from tunes_player.core.library.store import FileMetadata
from tunes_player.core.playback_quality import (
    local_file_format_label,
    qobuz_format_label_from_stream,
    qobuz_stream_format_label,
    tidal_format_label,
    tidal_format_label_from_stream_payload,
    tidal_stream_format_label,
)


class PlaybackQualityTests(unittest.TestCase):
    def test_lossless_flac(self) -> None:
        meta = FileMetadata(
            path="/a.flac",
            codec="flac",
            duration_sec=200.0,
            sample_rate=96000,
            bit_depth=24,
            channels=2,
        )
        self.assertEqual(local_file_format_label(meta), "24-bit / 96 kHz")

    def test_lossy_mp3(self) -> None:
        meta = FileMetadata(
            path="/a.mp3",
            codec="mp3",
            duration_sec=200.0,
            sample_rate=44100,
            bit_depth=None,
            channels=2,
        )
        self.assertEqual(local_file_format_label(meta), "MP3")

    def test_qobuz_labels(self) -> None:
        self.assertEqual(qobuz_stream_format_label(5), "MP3 320")
        self.assertEqual(qobuz_stream_format_label(27), "24-bit / 192 kHz")

    def test_qobuz_stream_label_uses_actual_stream_not_format_ceiling(self) -> None:
        self.assertEqual(
            qobuz_format_label_from_stream(
                {"bit_depth": 16, "sampling_rate": 44.1},
                fallback_format_id=27,
            ),
            "16-bit / 44.1 kHz",
        )

    def test_qobuz_stream_label_hi_res(self) -> None:
        self.assertEqual(
            qobuz_format_label_from_stream(
                {"bit_depth": 24, "sampling_rate": 192},
                fallback_format_id=27,
            ),
            "24-bit / 192 kHz",
        )

    def test_tidal_lossless(self) -> None:
        self.assertEqual(
            tidal_stream_format_label("LOSSLESS"),
            "16-bit / 44.1 kHz lossless",
        )

    def test_tidal_high_is_bitrate_not_codec(self) -> None:
        self.assertEqual(tidal_stream_format_label("HIGH"), "320 kbps")

    def test_tidal_stream_payload_hi_res(self) -> None:
        payload = {
            "audioQuality": "HI_RES_LOSSLESS",
            "bitDepth": 24,
            "sampleRate": 96000,
        }
        self.assertEqual(
            tidal_format_label_from_stream_payload(payload),
            "24-bit / 96 kHz lossless",
        )

    def test_tidal_track_metadata_fallback(self) -> None:
        self.assertEqual(
            tidal_format_label(audio_quality="HI_RES_LOSSLESS"),
            "24-bit / 96 kHz lossless",
        )


if __name__ == "__main__":
    unittest.main()
