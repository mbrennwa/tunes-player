"""Tests for release catalog quality tiers."""

from __future__ import annotations

import unittest

from types import SimpleNamespace

from tunes_player.core.release_quality import (
    QUALITY_FILTER_CD,
    QUALITY_FILTER_COMPRESSED,
    QUALITY_FILTER_HI_RES,
    max_quality_tier,
    tier_from_local,
    tier_from_qobuz_album,
    tier_from_tidal_album,
    tier_from_tidal_peak,
    tier_from_tidal_track,
)


class ReleaseQualityTests(unittest.TestCase):
    def test_local_mp3_is_compressed(self) -> None:
        self.assertEqual(
            tier_from_local(
                max_bit_depth=None,
                max_sample_rate=44100,
                has_lossless=False,
                has_lossy=True,
            ),
            QUALITY_FILTER_COMPRESSED,
        )

    def test_local_flac_16_44_is_cd(self) -> None:
        self.assertEqual(
            tier_from_local(
                max_bit_depth=16,
                max_sample_rate=44100,
                has_lossless=True,
                has_lossy=False,
            ),
            QUALITY_FILTER_CD,
        )

    def test_local_flac_24_96_is_hi_res(self) -> None:
        self.assertEqual(
            tier_from_local(
                max_bit_depth=24,
                max_sample_rate=96000,
                has_lossless=True,
                has_lossy=False,
            ),
            QUALITY_FILTER_HI_RES,
        )

    def test_local_mixed_peak_is_cd_when_lossless_cd_only(self) -> None:
        self.assertEqual(
            tier_from_local(
                max_bit_depth=16,
                max_sample_rate=44100,
                has_lossless=True,
                has_lossy=True,
            ),
            QUALITY_FILTER_CD,
        )

    def test_tidal_album_metadata_hi_res(self) -> None:
        album = SimpleNamespace(
            audio_quality="HI_RES_LOSSLESS",
            media_metadata_tags=["HIRES_LOSSLESS"],
            audio_modes=[],
        )
        self.assertEqual(tier_from_tidal_album(album), QUALITY_FILTER_HI_RES)

    def test_tidal_album_without_metadata_is_unknown(self) -> None:
        album = SimpleNamespace(audio_quality="", media_metadata_tags=None, audio_modes=[])
        self.assertEqual(tier_from_tidal_album(album), "")

    def test_tidal_track_metadata_hi_res(self) -> None:
        track = SimpleNamespace(
            audio_quality="HI_RES_LOSSLESS",
            media_metadata_tags=["HIRES_LOSSLESS"],
        )
        self.assertEqual(tier_from_tidal_track(track), QUALITY_FILTER_HI_RES)

    def test_tidal_album_metadata_cd(self) -> None:
        album = SimpleNamespace(
            audio_quality="LOSSLESS",
            media_metadata_tags=None,
            audio_modes=[],
        )
        self.assertEqual(tier_from_tidal_album(album), QUALITY_FILTER_CD)

    def test_tidal_album_audio_modes_hi_res(self) -> None:
        album = SimpleNamespace(
            audio_quality="LOSSLESS",
            media_metadata_tags=None,
            audio_modes=["HI_RES_LOSSLESS"],
        )
        self.assertEqual(tier_from_tidal_album(album), QUALITY_FILTER_HI_RES)

    def test_max_quality_tier(self) -> None:
        self.assertEqual(
            max_quality_tier(QUALITY_FILTER_CD, QUALITY_FILTER_HI_RES),
            QUALITY_FILTER_HI_RES,
        )

    def test_tidal_peak_rank_mapping(self) -> None:
        self.assertEqual(tier_from_tidal_peak(0), QUALITY_FILTER_COMPRESSED)
        self.assertEqual(tier_from_tidal_peak(1), QUALITY_FILTER_COMPRESSED)
        self.assertEqual(tier_from_tidal_peak(2), QUALITY_FILTER_CD)
        self.assertEqual(tier_from_tidal_peak(3), QUALITY_FILTER_HI_RES)
        self.assertEqual(tier_from_tidal_peak(4), QUALITY_FILTER_HI_RES)

    def test_qobuz_hi_res_album(self) -> None:
        album = {
            "maximum_bit_depth": 24,
            "maximum_sampling_rate": 96000,
            "hires": True,
        }
        self.assertEqual(tier_from_qobuz_album(album), QUALITY_FILTER_HI_RES)

    def test_qobuz_cd_album(self) -> None:
        album = {
            "maximum_bit_depth": 16,
            "maximum_sampling_rate": 44100,
            "hires": False,
        }
        self.assertEqual(tier_from_qobuz_album(album), QUALITY_FILTER_CD)

    def test_qobuz_cd_album_khz_sample_rate(self) -> None:
        album = {
            "maximum_bit_depth": 16,
            "maximum_sampling_rate": 44.1,
            "hires": False,
        }
        self.assertEqual(tier_from_qobuz_album(album), QUALITY_FILTER_CD)

    def test_qobuz_hi_res_album_khz_sample_rate(self) -> None:
        album = {
            "maximum_bit_depth": 24,
            "maximum_sampling_rate": 192,
            "hires": False,
        }
        self.assertEqual(tier_from_qobuz_album(album), QUALITY_FILTER_HI_RES)

    def test_qobuz_compressed_album(self) -> None:
        mp3_only = {"maximum_bit_depth": None, "maximum_sampling_rate": None}
        self.assertEqual(tier_from_qobuz_album(mp3_only), QUALITY_FILTER_COMPRESSED)

    def test_qobuz_technical_specifications_hi_res(self) -> None:
        album = {
            "maximum_technical_specifications": "24bit/192kHz Stereo",
            "maximum_bit_depth": 16,
            "maximum_sampling_rate": 44.1,
        }
        self.assertEqual(tier_from_qobuz_album(album), QUALITY_FILTER_HI_RES)

    def test_qobuz_embedded_track_raises_tier(self) -> None:
        album = {
            "maximum_bit_depth": 16,
            "maximum_sampling_rate": 44.1,
            "tracks": {
                "items": [
                    {
                        "maximum_bit_depth": 24,
                        "maximum_sampling_rate": 192,
                    },
                ],
            },
        }
        self.assertEqual(tier_from_qobuz_album(album), QUALITY_FILTER_HI_RES)


if __name__ == "__main__":
    unittest.main()
