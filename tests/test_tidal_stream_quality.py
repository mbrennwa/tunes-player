"""Tests for TIDAL stream quality negotiation."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from tunes_player.core.backends.tidal.stream_quality import (
    cap_session_quality_for_ceiling,
    negotiate_stream_payload,
    playback_quality_candidates,
    session_quality_for_subscription,
    subscription_allows_hi_res,
    track_peak_quality,
)
from tunes_player.core.release_quality import QUALITY_FILTER_CD


class TidalStreamQualityTests(unittest.TestCase):
    def test_session_quality_hifi_defaults_lossless(self) -> None:
        self.assertEqual(session_quality_for_subscription(hi_res_entitled=False), "LOSSLESS")

    def test_session_quality_hifi_plus(self) -> None:
        self.assertEqual(
            session_quality_for_subscription(hi_res_entitled=True),
            "HI_RES_LOSSLESS",
        )

    def test_cd_track_candidates_skip_hi_res_first_when_session_hi_res(self) -> None:
        track = SimpleNamespace(audio_quality="LOSSLESS", media_metadata_tags=None)
        self.assertEqual(
            playback_quality_candidates("HI_RES_LOSSLESS", track),
            ["LOSSLESS", "HI_RES", "HIGH"],
        )

    def test_cd_ceiling_caps_hi_res_session_and_track(self) -> None:
        track = SimpleNamespace(
            audio_quality="HI_RES_LOSSLESS",
            media_metadata_tags=["HIRES_LOSSLESS"],
        )
        self.assertEqual(
            cap_session_quality_for_ceiling("HI_RES_LOSSLESS", QUALITY_FILTER_CD),
            "LOSSLESS",
        )
        self.assertEqual(
            playback_quality_candidates(
                "HI_RES_LOSSLESS",
                track,
                ceiling_tier=QUALITY_FILTER_CD,
            ),
            ["LOSSLESS", "HIGH"],
        )

    def test_hi_res_track_candidates(self) -> None:
        track = SimpleNamespace(
            audio_quality="HI_RES_LOSSLESS",
            media_metadata_tags=["HIRES_LOSSLESS"],
        )
        self.assertEqual(
            playback_quality_candidates("HI_RES_LOSSLESS", track),
            ["HI_RES_LOSSLESS", "LOSSLESS", "HIGH"],
        )

    def test_hifi_session_candidates(self) -> None:
        track = SimpleNamespace(audio_quality="LOSSLESS", media_metadata_tags=None)
        self.assertEqual(
            playback_quality_candidates("LOSSLESS", track),
            ["LOSSLESS", "HIGH"],
        )

    def test_catalog_high_still_tries_lossless(self) -> None:
        """Catalog HIGH is unreliable; paid sessions should try LOSSLESS first."""
        track = SimpleNamespace(audio_quality="HIGH", media_metadata_tags=None)
        self.assertEqual(
            playback_quality_candidates("LOSSLESS", track),
            ["LOSSLESS", "HIGH"],
        )

    def test_cd_track_requests_lossless_only(self) -> None:
        calls: list[str] = []

        def request(quality: str) -> dict:
            calls.append(quality)
            return {
                "audioQuality": "LOSSLESS",
                "bitDepth": 16,
                "sampleRate": 44100,
            }

        track = SimpleNamespace(audio_quality="LOSSLESS", media_metadata_tags=None)
        candidates = playback_quality_candidates("HI_RES_LOSSLESS", track)
        payload, chosen = negotiate_stream_payload(candidates, request)
        self.assertEqual(calls, ["LOSSLESS"])
        self.assertEqual(chosen, "LOSSLESS")

    def test_negotiate_retries_high_with_lossless(self) -> None:
        calls: list[str] = []

        def request(quality: str) -> dict:
            calls.append(quality)
            if quality == "HI_RES_LOSSLESS":
                return {"audioQuality": "HIGH"}
            return {
                "audioQuality": "LOSSLESS",
                "bitDepth": 16,
                "sampleRate": 44100,
            }

        track = SimpleNamespace(
            audio_quality="HI_RES_LOSSLESS",
            media_metadata_tags=["HIRES_LOSSLESS"],
        )
        candidates = playback_quality_candidates("HI_RES_LOSSLESS", track)
        payload, chosen = negotiate_stream_payload(candidates, request)
        self.assertEqual(calls, ["HI_RES_LOSSLESS", "LOSSLESS"])
        self.assertEqual(chosen, "LOSSLESS")
        self.assertEqual(payload["audioQuality"], "LOSSLESS")

    def test_subscription_hi_res_json(self) -> None:
        self.assertTrue(
            subscription_allows_hi_res({"highestSoundQuality": "HI_RES_LOSSLESS"})
        )
        self.assertFalse(subscription_allows_hi_res({"highestSoundQuality": "LOSSLESS"}))

    def test_track_peak_from_tags(self) -> None:
        track = SimpleNamespace(audio_quality="HIGH", media_metadata_tags=["LOSSLESS"])
        self.assertGreaterEqual(track_peak_quality(track), 2)


if __name__ == "__main__":
    unittest.main()
