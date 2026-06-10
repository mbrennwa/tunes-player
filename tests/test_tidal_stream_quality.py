"""Tests for TIDAL stream quality negotiation."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from tunes_player.core.backends.tidal.stream_quality import (
    cap_session_quality_for_preference,
    negotiate_stream_payload,
    payload_is_hi_res_stream,
    playback_quality_candidates,
    session_quality_for_subscription,
    subscription_allows_hi_res,
    track_peak_quality,
)
from tunes_player.core.release_quality import (
    QUALITY_FILTER_CD,
    QUALITY_FILTER_HI_RES,
    playback_preference_from_shell,
)

_ALL_PREFERENCE = playback_preference_from_shell(frozenset())
_CD_PREFERENCE = playback_preference_from_shell(frozenset({QUALITY_FILTER_CD}))
_HI_RES_PREFERENCE = playback_preference_from_shell(
    frozenset({QUALITY_FILTER_HI_RES}),
)


class TidalStreamQualityTests(unittest.TestCase):
    def test_session_quality_hifi_defaults_lossless(self) -> None:
        self.assertEqual(session_quality_for_subscription(hi_res_entitled=False), "LOSSLESS")

    def test_session_quality_hifi_plus(self) -> None:
        self.assertEqual(
            session_quality_for_subscription(hi_res_entitled=True),
            "HI_RES_LOSSLESS",
        )

    def test_cd_track_candidates_respect_cd_preference_on_hi_res_session(self) -> None:
        track = SimpleNamespace(audio_quality="LOSSLESS", media_metadata_tags=None)
        self.assertEqual(
            playback_quality_candidates(
                "HI_RES_LOSSLESS",
                track,
                preference=_CD_PREFERENCE,
            ),
            ["LOSSLESS", "HIGH"],
        )

    def test_cd_preference_caps_hi_res_session_and_track(self) -> None:
        track = SimpleNamespace(
            audio_quality="HI_RES_LOSSLESS",
            media_metadata_tags=["HIRES_LOSSLESS"],
        )
        self.assertEqual(
            cap_session_quality_for_preference("HI_RES_LOSSLESS", _CD_PREFERENCE),
            "LOSSLESS",
        )
        self.assertEqual(
            playback_quality_candidates(
                "HI_RES_LOSSLESS",
                track,
                preference=_CD_PREFERENCE,
            ),
            ["LOSSLESS", "HIGH"],
        )

    def test_hi_res_track_candidates(self) -> None:
        track = SimpleNamespace(
            audio_quality="HI_RES_LOSSLESS",
            media_metadata_tags=["HIRES_LOSSLESS"],
        )
        self.assertEqual(
            playback_quality_candidates(
                "HI_RES_LOSSLESS",
                track,
                preference=_ALL_PREFERENCE,
            ),
            ["HI_RES_LOSSLESS", "HI_RES", "LOSSLESS", "HIGH"],
        )

    def test_hifi_session_candidates(self) -> None:
        track = SimpleNamespace(audio_quality="LOSSLESS", media_metadata_tags=None)
        self.assertEqual(
            playback_quality_candidates("LOSSLESS", track, preference=_ALL_PREFERENCE),
            ["LOSSLESS", "HIGH"],
        )

    def test_catalog_high_still_tries_lossless(self) -> None:
        """Catalog HIGH is unreliable; paid sessions should try LOSSLESS first."""
        track = SimpleNamespace(audio_quality="HIGH", media_metadata_tags=None)
        self.assertEqual(
            playback_quality_candidates("LOSSLESS", track, preference=_ALL_PREFERENCE),
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
        candidates = playback_quality_candidates(
            "HI_RES_LOSSLESS",
            track,
            preference=_CD_PREFERENCE,
        )
        payload, chosen = negotiate_stream_payload(candidates, request)
        self.assertEqual(calls, ["LOSSLESS"])
        self.assertEqual(chosen, "LOSSLESS")

    def test_negotiate_retries_high_with_lossless(self) -> None:
        calls: list[str] = []

        def request(quality: str) -> dict:
            calls.append(quality)
            if quality in ("HI_RES_LOSSLESS", "HI_RES"):
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
        candidates = playback_quality_candidates(
            "HI_RES_LOSSLESS",
            track,
            preference=_ALL_PREFERENCE,
        )
        payload, chosen = negotiate_stream_payload(candidates, request)
        self.assertEqual(calls, ["HI_RES_LOSSLESS", "HI_RES", "LOSSLESS"])
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

    def test_hi_res_preference_prepends_hi_res_on_cd_track(self) -> None:
        track = SimpleNamespace(audio_quality="LOSSLESS", media_metadata_tags=None)
        self.assertEqual(
            playback_quality_candidates(
                "HI_RES_LOSSLESS",
                track,
                preference=_HI_RES_PREFERENCE,
            ),
            ["HI_RES_LOSSLESS", "HI_RES", "LOSSLESS", "HIGH"],
        )

    def test_payload_is_hi_res_stream_uses_sample_rate_not_api_label(self) -> None:
        self.assertFalse(
            payload_is_hi_res_stream(
                {
                    "audioQuality": "HI_RES_LOSSLESS",
                    "bitDepth": 24,
                    "sampleRate": 44100,
                },
            ),
        )
        self.assertTrue(
            payload_is_hi_res_stream(
                {
                    "audioQuality": "LOSSLESS",
                    "bitDepth": 24,
                    "sampleRate": 96000,
                },
            ),
        )

    def test_negotiate_retries_cd_response_to_hi_res_request(self) -> None:
        calls: list[str] = []

        def request(quality: str) -> dict:
            calls.append(quality)
            if quality == "HI_RES_LOSSLESS":
                return {
                    "audioQuality": "LOSSLESS",
                    "bitDepth": 16,
                    "sampleRate": 44100,
                }
            return {
                "audioQuality": "HI_RES_LOSSLESS",
                "bitDepth": 24,
                "sampleRate": 96000,
            }

        track = SimpleNamespace(audio_quality="LOSSLESS", media_metadata_tags=None)
        candidates = playback_quality_candidates(
            "HI_RES_LOSSLESS",
            track,
            preference=_HI_RES_PREFERENCE,
        )
        payload, chosen = negotiate_stream_payload(
            candidates,
            request,
            preference=_HI_RES_PREFERENCE,
        )
        self.assertEqual(calls, ["HI_RES_LOSSLESS", "HI_RES"])
        self.assertEqual(chosen, "HI_RES")
        self.assertEqual(payload["sampleRate"], 96000)

    def test_hi_res_preference_includes_lossless_fallback(self) -> None:
        track = SimpleNamespace(audio_quality="LOSSLESS", media_metadata_tags=None)
        self.assertEqual(
            playback_quality_candidates(
                "HI_RES_LOSSLESS",
                track,
                preference=_HI_RES_PREFERENCE,
            ),
            ["HI_RES_LOSSLESS", "HI_RES", "LOSSLESS", "HIGH"],
        )

    def test_negotiate_retries_lossless_cd_when_hi_res_remains(self) -> None:
        calls: list[str] = []

        def request(quality: str) -> dict:
            calls.append(quality)
            if quality == "LOSSLESS":
                return {
                    "audioQuality": "LOSSLESS",
                    "bitDepth": 16,
                    "sampleRate": 44100,
                }
            return {
                "audioQuality": "HI_RES",
                "bitDepth": 24,
                "sampleRate": 96000,
            }

        candidates = ["LOSSLESS", "HI_RES", "HIGH"]
        payload, chosen = negotiate_stream_payload(
            candidates,
            request,
            preference=_ALL_PREFERENCE,
        )
        self.assertEqual(calls, ["LOSSLESS", "HI_RES"])
        self.assertEqual(chosen, "HI_RES")
        self.assertEqual(payload["sampleRate"], 96000)


if __name__ == "__main__":
    unittest.main()
