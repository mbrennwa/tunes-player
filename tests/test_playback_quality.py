"""Tests for playback format labels."""

from __future__ import annotations

import unittest

from tunes_player.core.library.store import FileMetadata
from tunes_player.core.models import Release, Source
from tunes_player.core.playback_quality import (
    catalog_tile_quality_label,
    format_rate_bit_depth_compact,
    local_file_format_label,
    qobuz_format_label_from_stream,
    qobuz_stream_file_metadata,
    qobuz_stream_format_label,
    stream_file_metadata,
    tidal_format_label,
    tidal_format_label_from_stream_payload,
    tidal_stream_file_metadata,
    tidal_stream_format_label,
)
from tunes_player.core.release_quality import (
    QUALITY_FILTER_CD,
    QUALITY_FILTER_COMPRESSED,
    QUALITY_FILTER_HI_RES,
    catalog_quality_label_for_release,
)


class PlaybackQualityTests(unittest.TestCase):
    def test_format_rate_bit_depth_compact_infers_depth_from_rate(self) -> None:
        self.assertEqual(
            format_rate_bit_depth_compact(
                bit_depth=None,
                sample_rate_hz=96_000,
                quality_tier=QUALITY_FILTER_HI_RES,
            ),
            "96/24",
        )

    def test_format_rate_bit_depth_compact(self) -> None:
        self.assertEqual(
            format_rate_bit_depth_compact(bit_depth=16, sample_rate_hz=44_100),
            "44.1/16",
        )
        self.assertEqual(
            format_rate_bit_depth_compact(bit_depth=24, sample_rate_hz=192_000),
            "192/24",
        )
        self.assertEqual(
            format_rate_bit_depth_compact(bit_depth=24, sample_rate_hz=96_000),
            "96/24",
        )

    def test_catalog_tile_quality_label_lossless(self) -> None:
        self.assertEqual(
            catalog_tile_quality_label(
                bit_depth=24,
                sample_rate_hz=96_000,
                quality_tier=QUALITY_FILTER_HI_RES,
            ),
            "96/24",
        )

    def test_catalog_tile_quality_label_cd_fallback(self) -> None:
        self.assertEqual(
            catalog_tile_quality_label(
                bit_depth=None,
                sample_rate_hz=None,
                quality_tier=QUALITY_FILTER_CD,
            ),
            "44.1/16",
        )

    def test_catalog_tile_quality_label_compressed(self) -> None:
        self.assertEqual(
            catalog_tile_quality_label(
                bit_depth=None,
                sample_rate_hz=None,
                quality_tier=QUALITY_FILTER_COMPRESSED,
                source=Source.QOBUZ,
            ),
            "MP3",
        )
        self.assertEqual(
            catalog_tile_quality_label(
                bit_depth=None,
                sample_rate_hz=None,
                quality_tier=QUALITY_FILTER_COMPRESSED,
                source=Source.TIDAL,
            ),
            "AAC",
        )
        self.assertEqual(
            catalog_tile_quality_label(
                bit_depth=None,
                sample_rate_hz=None,
                quality_tier=QUALITY_FILTER_COMPRESSED,
                lossy_codec="aac",
            ),
            "AAC",
        )

    def test_catalog_quality_label_for_release(self) -> None:
        release = Release(
            id="tidal:album:1@hi_res",
            title="Album",
            artist_name="Artist",
            source=Source.TIDAL,
            quality_tier=QUALITY_FILTER_HI_RES,
            peak_bit_depth=24,
            peak_sample_rate_hz=192_000,
        )
        self.assertEqual(catalog_quality_label_for_release(release), "192/24")

    def test_catalog_quality_label_hi_res_claim_with_cd_rate_uses_acoustics(self) -> None:
        """Marketing hi_res + 44.1/24 peak must label as 44.1/24, not blank (#147)."""
        release = Release(
            id="tidal:album:man-down@hi_res",
            title="MAN DOWN",
            artist_name="B Young",
            source=Source.TIDAL,
            quality_tier=QUALITY_FILTER_HI_RES,
            peak_quality_tier=QUALITY_FILTER_HI_RES,
            available_quality_tiers=frozenset({QUALITY_FILTER_HI_RES}),
            peak_bit_depth=24,
            peak_sample_rate_hz=44_100,
        )
        self.assertEqual(catalog_quality_label_for_release(release), "44.1/24")

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

    def test_stream_file_metadata_normalizes_qobuz_khz(self) -> None:
        meta = qobuz_stream_file_metadata({"bit_depth": 24, "sampling_rate": 44.1})
        assert meta is not None
        self.assertEqual(meta.sample_rate, 44100)
        self.assertEqual(meta.bit_depth, 24)
        self.assertEqual(meta.codec, "flac")

    def test_qobuz_stream_file_metadata_mp3_format_id(self) -> None:
        meta = qobuz_stream_file_metadata({"format_id": 5, "url": "https://x/a"})
        assert meta is not None
        self.assertEqual(meta.codec, "mp3")
        self.assertIsNone(meta.sample_rate)
        self.assertIsNone(meta.bit_depth)

    def test_qobuz_stream_file_metadata_flac_format_ids(self) -> None:
        for format_id in (6, 7, 27):
            meta = qobuz_stream_file_metadata(
                {
                    "format_id": format_id,
                    "bit_depth": 16,
                    "sampling_rate": 44.1,
                }
            )
            assert meta is not None
            self.assertEqual(meta.codec, "flac", format_id)

    def test_tidal_stream_file_metadata_uses_hz(self) -> None:
        meta = tidal_stream_file_metadata(
            {"bitDepth": 16, "sampleRate": 44100, "audioQuality": "LOSSLESS"}
        )
        assert meta is not None
        self.assertEqual(meta.sample_rate, 44100)
        self.assertEqual(meta.bit_depth, 16)
        self.assertEqual(meta.codec, "flac")

    def test_tidal_stream_file_metadata_lossy_without_depth(self) -> None:
        for quality in ("HIGH", "LOW", "AUDIOQUALITY.HIGH"):
            meta = tidal_stream_file_metadata({"audioQuality": quality})
            assert meta is not None
            self.assertEqual(meta.codec, "aac", quality)

    def test_stream_file_metadata_codec_only(self) -> None:
        meta = stream_file_metadata(codec="mp3")
        assert meta is not None
        self.assertEqual(meta.codec, "mp3")

    def test_stream_file_metadata_returns_none_without_fields(self) -> None:
        self.assertIsNone(stream_file_metadata())


if __name__ == "__main__":
    unittest.main()
