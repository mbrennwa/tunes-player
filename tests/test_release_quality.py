"""Tests for release catalog quality tiers."""

from __future__ import annotations

import unittest

from types import SimpleNamespace

from tunes_player.core.release_quality import (
    QUALITY_FILTER_CD,
    QUALITY_FILTER_COMPRESSED,
    QUALITY_FILTER_HI_RES,
    PlaybackPreference,
    acoustic_tier_from_lossless,
    acoustic_tier_from_stream,
    classify_local_catalog,
    classify_qobuz_catalog,
    classify_tidal_catalog,
    is_acoustic_hi_res,
    max_quality_tier,
    min_quality_tier,
    playback_preference_for_tier,
    playback_preference_from_shell,
    qobuz_format_candidates_for_preference,
    qobuz_format_id_for_preference,
    release_matches_quality_filter,
    streaming_catalog_quality_needs_enrich,
    tier_from_local,
    tier_from_qobuz_album,
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

    def test_tidal_album_measured_cd_rate_stays_cd_despite_hires_tags(self) -> None:
        album = SimpleNamespace(
            audio_quality="HI_RES_LOSSLESS",
            media_metadata_tags=["HIRES_LOSSLESS"],
            audio_modes=[],
            sample_rate=44_100,
        )
        tiers = classify_tidal_catalog(album)
        self.assertIn(QUALITY_FILTER_CD, tiers)

    def test_tidal_album_without_metadata_has_no_tier(self) -> None:
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

    def test_tidal_hi_res_edition_uses_media_tags(self) -> None:
        """Separate hi-res catalog IDs expose HIRES_LOSSLESS in media tags."""
        album = SimpleNamespace(
            audio_quality="LOSSLESS",
            media_metadata_tags=["LOSSLESS", "HIRES_LOSSLESS"],
            audio_modes=["STEREO"],
        )
        self.assertEqual(tier_from_tidal_album(album), QUALITY_FILTER_HI_RES)
        self.assertEqual(
            classify_tidal_catalog(album),
            frozenset({QUALITY_FILTER_HI_RES}),
        )

    def test_tidal_cd_edition_lossless_tags_only(self) -> None:
        album = SimpleNamespace(
            audio_quality="LOSSLESS",
            media_metadata_tags=["LOSSLESS"],
            audio_modes=["STEREO"],
        )
        self.assertEqual(tier_from_tidal_album(album), QUALITY_FILTER_CD)

    def test_tidal_probe_failure_does_not_default_to_cd(self) -> None:
        """Broken stream probe must not classify a hi-res edition as CD."""
        album = SimpleNamespace(
            audio_quality="LOSSLESS",
            media_metadata_tags=["LOSSLESS", "HIRES_LOSSLESS"],
            audio_modes=["STEREO"],
            get_audio_resolution=lambda: (_ for _ in ()).throw(RuntimeError("rate limited")),
        )
        self.assertEqual(tier_from_tidal_album(album), QUALITY_FILTER_HI_RES)
        self.assertNotIn(
            QUALITY_FILTER_CD,
            classify_tidal_catalog(album),
        )

    def test_tidal_batch_style_cd_filter_leaves_one_death_magnetic(self) -> None:
        from tunes_player.core.release_quality_tiles import expand_releases_by_quality_tier
        from tunes_player.core.shell_state import apply_quality_filter

        cd_album = SimpleNamespace(
            audio_quality="LOSSLESS",
            media_metadata_tags=["LOSSLESS"],
            audio_modes=["STEREO"],
        )
        hi_res_album = SimpleNamespace(
            audio_quality="LOSSLESS",
            media_metadata_tags=["LOSSLESS", "HIRES_LOSSLESS"],
            audio_modes=["STEREO"],
        )
        enriched = [
            Release(
                id="tidal:album:1",
                title="Death Magnetic",
                artist_name="Metallica",
                source=Source.TIDAL,
                peak_quality_tier=QUALITY_FILTER_CD,
                available_quality_tiers=classify_tidal_catalog(cd_album),
                catalog_quality_ready=True,
                peak_sample_rate_hz=44_100,
            ),
            Release(
                id="tidal:album:2",
                title="Death Magnetic",
                artist_name="Metallica",
                source=Source.TIDAL,
                peak_quality_tier=QUALITY_FILTER_HI_RES,
                available_quality_tiers=classify_tidal_catalog(hi_res_album),
                catalog_quality_ready=True,
                peak_sample_rate_hz=96_000,
            ),
        ]
        expanded = expand_releases_by_quality_tier(enriched)
        filtered = apply_quality_filter(expanded, frozenset({QUALITY_FILTER_CD}))
        self.assertEqual(
            sum(1 for release in filtered if release.title == "Death Magnetic"),
            1,
        )

    def test_stale_tidal_enrich_missing_sample_rate(self) -> None:
        stale = Release(
            id="tidal:album:1",
            title="Album",
            artist_name="Artist",
            source=Source.TIDAL,
            peak_quality_tier=QUALITY_FILTER_CD,
            available_quality_tiers=frozenset({QUALITY_FILTER_CD}),
            catalog_quality_ready=True,
            peak_sample_rate_hz=None,
        )
        self.assertTrue(streaming_catalog_quality_needs_enrich(stale))
        fresh = Release(
            id="tidal:album:2",
            title="Album",
            artist_name="Artist",
            source=Source.TIDAL,
            peak_quality_tier=QUALITY_FILTER_HI_RES,
            available_quality_tiers=frozenset({QUALITY_FILTER_HI_RES}),
            catalog_quality_ready=True,
            peak_sample_rate_hz=96_000,
        )
        self.assertFalse(streaming_catalog_quality_needs_enrich(fresh))

    def test_streaming_catalog_does_not_re_enrich_for_missing_genre(self) -> None:
        release = Release(
            id="tidal:album:3",
            title="Album",
            artist_name="Artist",
            source=Source.TIDAL,
            catalog_quality_ready=True,
            peak_sample_rate_hz=96_000,
            genre=None,
        )
        self.assertFalse(streaming_catalog_quality_needs_enrich(release))

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

    def test_qobuz_unknown_metadata_has_no_tier(self) -> None:
        mp3_only = {"maximum_bit_depth": None, "maximum_sampling_rate": None}
        self.assertEqual(tier_from_qobuz_album(mp3_only), "")
        self.assertEqual(classify_qobuz_catalog(mp3_only), frozenset())

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

    def test_playback_preference_from_shell_cd_only(self) -> None:
        pref = playback_preference_from_shell(frozenset({QUALITY_FILTER_CD}))
        self.assertEqual(pref.max_tier, QUALITY_FILTER_CD)

    def test_playback_preference_from_shell_hi_res_only(self) -> None:
        pref = playback_preference_from_shell(frozenset({QUALITY_FILTER_HI_RES}))
        self.assertEqual(pref.max_tier, QUALITY_FILTER_HI_RES)

    def test_playback_preference_from_shell_picks_highest_enabled(self) -> None:
        pref = playback_preference_from_shell(
            frozenset({QUALITY_FILTER_CD, QUALITY_FILTER_HI_RES}),
        )
        self.assertEqual(pref.max_tier, QUALITY_FILTER_HI_RES)

    def test_playback_preference_empty_filter_means_hi_res(self) -> None:
        pref = playback_preference_from_shell(frozenset())
        self.assertEqual(pref.max_tier, QUALITY_FILTER_HI_RES)

    def test_playback_preference_for_tier(self) -> None:
        self.assertEqual(
            playback_preference_for_tier(QUALITY_FILTER_CD).max_tier,
            QUALITY_FILTER_CD,
        )
        self.assertEqual(
            playback_preference_for_tier(QUALITY_FILTER_HI_RES).max_tier,
            QUALITY_FILTER_HI_RES,
        )

    def test_qobuz_format_id_cd_preference_caps_config(self) -> None:
        preference = PlaybackPreference(max_tier=QUALITY_FILTER_CD)
        self.assertEqual(
            qobuz_format_id_for_preference(config_format_id=27, preference=preference),
            6,
        )

    def test_qobuz_format_id_compressed_preference(self) -> None:
        preference = PlaybackPreference(max_tier=QUALITY_FILTER_COMPRESSED)
        self.assertEqual(
            qobuz_format_id_for_preference(config_format_id=6, preference=preference),
            5,
        )

    def test_qobuz_format_id_hi_res_preference(self) -> None:
        preference = PlaybackPreference(max_tier=QUALITY_FILTER_HI_RES)
        self.assertEqual(
            qobuz_format_id_for_preference(config_format_id=27, preference=preference),
            27,
        )

    def test_classify_local_no_guess_when_empty(self) -> None:
        self.assertEqual(
            classify_local_catalog(
                max_bit_depth=None,
                max_sample_rate=None,
                has_lossless=False,
                has_lossy=False,
            ),
            frozenset(),
        )

    def test_release_has_tier_properties(self) -> None:
        release = Release(
            id="local:1",
            title="Album",
            artist_name="Artist",
            source=Source.LOCAL,
            available_quality_tiers=frozenset(
                {QUALITY_FILTER_COMPRESSED, QUALITY_FILTER_CD, QUALITY_FILTER_HI_RES},
            ),
        )
        self.assertTrue(release.has_compressed)
        self.assertTrue(release.has_cd)
        self.assertTrue(release.has_hires)

    def test_qobuz_format_candidates_for_preference(self) -> None:
        all_pref = playback_preference_from_shell(frozenset())
        self.assertEqual(
            qobuz_format_candidates_for_preference(
                config_format_id=27,
                preference=all_pref,
            ),
            [27, 7, 6, 5],
        )
        cd_pref = PlaybackPreference(max_tier=QUALITY_FILTER_CD)
        self.assertEqual(
            qobuz_format_candidates_for_preference(
                config_format_id=27,
                preference=cd_pref,
            ),
            [6, 5],
        )

    def test_qobuz_hires_streamable_at_cd_peak_stays_cd_only(self) -> None:
        album = {
            "maximum_bit_depth": 16,
            "maximum_sampling_rate": 44.1,
            "hires_streamable": True,
        }
        self.assertEqual(tier_from_qobuz_album(album), QUALITY_FILTER_CD)
        self.assertEqual(classify_qobuz_catalog(album), frozenset({QUALITY_FILTER_CD}))

    def test_qobuz_dual_format_matches_hi_res_filter(self) -> None:
        release = Release(
            id="qobuz:album:1",
            title="Album",
            artist_name="Artist",
            source=Source.QOBUZ,
            peak_quality_tier=QUALITY_FILTER_CD,
            catalog_quality_ready=True,
            available_quality_tiers=classify_qobuz_catalog(
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
