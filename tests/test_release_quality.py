"""Tests for release catalog quality tiers."""

from __future__ import annotations

import unittest

from types import SimpleNamespace

from tunes_player.core.release_quality import (
    QUALITY_FILTER_CD,
    QUALITY_FILTER_COMPRESSED,
    QUALITY_FILTER_HI_RES,
    PlaybackQualityPolicy,
    acoustic_tier_from_lossless,
    acoustic_tier_from_stream,
    is_acoustic_hi_res,
    max_quality_tier,
    min_quality_tier,
    playback_policy_for_play,
    qobuz_format_id_for_policy,
    release_matches_quality_filter,
    tier_from_local,
    tier_from_qobuz_album,
    tiers_from_qobuz_album,
    tier_from_tidal_album,
    tier_from_tidal_peak,
    tier_from_tidal_track,
)
from tunes_player.core.models import Release, Source


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

    def test_local_flac_24_44_is_cd(self) -> None:
        self.assertEqual(
            tier_from_local(
                max_bit_depth=24,
                max_sample_rate=44100,
                has_lossless=True,
                has_lossy=False,
            ),
            QUALITY_FILTER_CD,
        )

    def test_local_flac_24_48_is_hi_res(self) -> None:
        self.assertEqual(
            tier_from_local(
                max_bit_depth=24,
                max_sample_rate=48000,
                has_lossless=True,
                has_lossy=False,
            ),
            QUALITY_FILTER_HI_RES,
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

    def test_acoustic_tier_24_44_is_cd(self) -> None:
        self.assertEqual(
            acoustic_tier_from_lossless(bit_depth=24, sample_rate_hz=44100),
            QUALITY_FILTER_CD,
        )

    def test_is_acoustic_hi_res_requires_above_cd_rate(self) -> None:
        self.assertFalse(is_acoustic_hi_res(44100))
        self.assertTrue(is_acoustic_hi_res(48000))

    def test_acoustic_tier_from_stream_lossy_is_compressed(self) -> None:
        self.assertEqual(
            acoustic_tier_from_stream(
                bit_depth=24,
                sample_rate_hz=96000,
                lossless=False,
            ),
            QUALITY_FILTER_COMPRESSED,
        )

    def test_tidal_album_api_hi_res_label_at_cd_rate_is_cd(self) -> None:
        album = SimpleNamespace(
            audio_quality="HI_RES_LOSSLESS",
            media_metadata_tags=["HIRES_LOSSLESS"],
            audio_modes=[],
        )
        self.assertEqual(tier_from_tidal_album(album), QUALITY_FILTER_CD)

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

    def test_tidal_album_audio_modes_dual_format(self) -> None:
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

    def test_qobuz_24_44_is_cd(self) -> None:
        album = {
            "maximum_bit_depth": 24,
            "maximum_sampling_rate": 44.1,
            "hires": False,
        }
        self.assertEqual(tier_from_qobuz_album(album), QUALITY_FILTER_CD)

    def test_qobuz_hires_flag_at_cd_rate_is_cd(self) -> None:
        album = {
            "maximum_bit_depth": 24,
            "maximum_sampling_rate": 44.1,
            "hires": True,
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

    def test_min_quality_tier(self) -> None:
        self.assertEqual(
            min_quality_tier(QUALITY_FILTER_CD, QUALITY_FILTER_HI_RES),
            QUALITY_FILTER_CD,
        )

    def test_playback_policy_cd_only(self) -> None:
        policy = playback_policy_for_play(
            enabled_quality_tiers=frozenset({QUALITY_FILTER_CD}),
            release=None,
        )
        self.assertEqual(policy.target_tier, QUALITY_FILTER_CD)
        self.assertEqual(policy.allowed_tiers, frozenset({QUALITY_FILTER_CD}))

    def test_playback_policy_hi_res_only(self) -> None:
        policy = playback_policy_for_play(
            enabled_quality_tiers=frozenset({QUALITY_FILTER_HI_RES}),
            release=None,
        )
        self.assertEqual(policy.target_tier, QUALITY_FILTER_HI_RES)
        self.assertEqual(policy.allowed_tiers, frozenset({QUALITY_FILTER_HI_RES}))

    def test_playback_policy_cd_and_hi_res(self) -> None:
        policy = playback_policy_for_play(
            enabled_quality_tiers=frozenset(
                {QUALITY_FILTER_CD, QUALITY_FILTER_HI_RES},
            ),
            release=None,
        )
        self.assertEqual(policy.target_tier, QUALITY_FILTER_HI_RES)
        self.assertEqual(
            policy.allowed_tiers,
            frozenset({QUALITY_FILTER_CD, QUALITY_FILTER_HI_RES}),
        )

    def test_playback_policy_empty_means_best_available(self) -> None:
        policy = playback_policy_for_play(
            enabled_quality_tiers=frozenset(),
            release=None,
        )
        self.assertIsNone(policy.target_tier)

    def test_playback_policy_cd_only_dual_format_release(self) -> None:
        release = Release(
            id="tidal:album:1",
            title="Album",
            artist_name="Artist",
            source=Source.TIDAL,
            peak_quality_tier=QUALITY_FILTER_CD,
            available_quality_tiers=frozenset(
                {
                    QUALITY_FILTER_COMPRESSED,
                    QUALITY_FILTER_CD,
                    QUALITY_FILTER_HI_RES,
                },
            ),
        )
        policy = playback_policy_for_play(
            enabled_quality_tiers=frozenset({QUALITY_FILTER_CD}),
            release=release,
        )
        self.assertEqual(policy.target_tier, QUALITY_FILTER_CD)

    def test_playback_policy_hi_res_only_dual_format_release(self) -> None:
        release = Release(
            id="tidal:album:1",
            title="Album",
            artist_name="Artist",
            source=Source.TIDAL,
            peak_quality_tier=QUALITY_FILTER_CD,
            available_quality_tiers=frozenset(
                {
                    QUALITY_FILTER_COMPRESSED,
                    QUALITY_FILTER_CD,
                    QUALITY_FILTER_HI_RES,
                },
            ),
        )
        policy = playback_policy_for_play(
            enabled_quality_tiers=frozenset({QUALITY_FILTER_HI_RES}),
            release=release,
        )
        self.assertEqual(policy.target_tier, QUALITY_FILTER_HI_RES)

    def test_qobuz_format_id_cd_policy_caps_config(self) -> None:
        policy = PlaybackQualityPolicy(
            target_tier=QUALITY_FILTER_CD,
            allowed_tiers=frozenset({QUALITY_FILTER_CD}),
        )
        self.assertEqual(
            qobuz_format_id_for_policy(config_format_id=27, policy=policy),
            6,
        )

    def test_qobuz_format_id_compressed_policy(self) -> None:
        policy = PlaybackQualityPolicy(
            target_tier=QUALITY_FILTER_COMPRESSED,
            allowed_tiers=frozenset({QUALITY_FILTER_COMPRESSED}),
        )
        self.assertEqual(
            qobuz_format_id_for_policy(config_format_id=6, policy=policy),
            5,
        )

    def test_qobuz_format_id_hi_res_policy(self) -> None:
        policy = PlaybackQualityPolicy(
            target_tier=QUALITY_FILTER_HI_RES,
            allowed_tiers=frozenset({QUALITY_FILTER_HI_RES}),
        )
        self.assertEqual(
            qobuz_format_id_for_policy(config_format_id=6, policy=policy),
            7,
        )

    def test_qobuz_hires_streamable_at_cd_peak_stays_cd_only(self) -> None:
        album = {
            "maximum_bit_depth": 16,
            "maximum_sampling_rate": 44.1,
            "hires_streamable": True,
        }
        self.assertEqual(tier_from_qobuz_album(album), QUALITY_FILTER_CD)
        self.assertEqual(
            tiers_from_qobuz_album(album),
            frozenset(
                {
                    QUALITY_FILTER_COMPRESSED,
                    QUALITY_FILTER_CD,
                },
            ),
        )

    def test_qobuz_dual_format_matches_hi_res_filter(self) -> None:
        release = Release(
            id="qobuz:album:1",
            title="Album",
            artist_name="Artist",
            source=Source.QOBUZ,
            peak_quality_tier=QUALITY_FILTER_CD,
            available_quality_tiers=tiers_from_qobuz_album(
                {
                    "maximum_bit_depth": 16,
                    "maximum_sampling_rate": 44.1,
                    "hires_streamable": True,
                    "tracks": {
                        "items": [
                            {
                                "maximum_bit_depth": 24,
                                "maximum_sampling_rate": 96,
                            },
                        ],
                    },
                },
            ),
        )
        self.assertTrue(
            release_matches_quality_filter(
                release,
                frozenset({QUALITY_FILTER_HI_RES}),
            ),
        )
        self.assertTrue(
            release_matches_quality_filter(
                release,
                frozenset({QUALITY_FILTER_CD}),
            ),
        )

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
