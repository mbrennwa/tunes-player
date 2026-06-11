"""Tests for provider album peak rate/depth extraction."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from tunes_player.core.backends.tidal.catalog_stream_probe import (
    clear_tidal_catalog_stream_probe_cache,
)
from tunes_player.core.release_catalog import (
    genre_from_tidal_album,
    genre_from_tidal_album_json,
    genre_from_tidal_openapi_payload,
    peak_bit_depth_from_qobuz_album,
    peak_rate_depth_from_qobuz_album,
    peak_rate_depth_from_tidal_album,
    peak_sample_rate_from_qobuz_album,
)


class QobuzPeakExtractionTests(unittest.TestCase):
    def test_reads_maximum_fields(self) -> None:
        album = {
            "maximum_bit_depth": 24,
            "maximum_sampling_rate": 192,
        }
        depth, rate = peak_rate_depth_from_qobuz_album(album)
        self.assertEqual(depth, 24)
        self.assertEqual(rate, 192_000)

    def test_parses_technical_specifications(self) -> None:
        album = {
            "maximum_technical_specifications": "24-bit / 96 kHz",
            "hires": True,
        }
        self.assertEqual(peak_sample_rate_from_qobuz_album(album), 96_000)
        self.assertEqual(peak_bit_depth_from_qobuz_album(album), 24)

    def test_prefers_highest_track_rate(self) -> None:
        album = {
            "maximum_sampling_rate": 44.1,
            "maximum_bit_depth": 16,
            "tracks": {
                "items": [
                    {"maximum_sampling_rate": 192, "maximum_bit_depth": 24},
                ],
            },
        }
        depth, rate = peak_rate_depth_from_qobuz_album(album)
        self.assertEqual(depth, 24)
        self.assertEqual(rate, 192_000)


class TidalGenreExtractionTests(unittest.TestCase):
    def test_parses_genre_list_from_album_json(self) -> None:
        data = {"genres": [{"name": "Pop/R&B"}]}
        self.assertEqual(genre_from_tidal_album_json(data), "Pop/R&B")

    def test_parses_openapi_track_genre_payload(self) -> None:
        payload = {
            "included": [
                {"type": "genres", "id": "1", "attributes": {"genreName": "Pop"}},
            ],
        }
        self.assertEqual(genre_from_tidal_openapi_payload(payload), "Pop")

    def test_fetches_genre_from_first_track_openapi(self) -> None:
        class _Response:
            ok = True

            @staticmethod
            def json() -> dict:
                return {
                    "included": [
                        {
                            "type": "genres",
                            "id": "2",
                            "attributes": {"genreName": "Rock"},
                        },
                    ],
                }

        class _Request:
            def request(self, method: str, path: str, **kwargs: object) -> _Response:
                self.last = (method, path, kwargs)
                return _Response()

        session = SimpleNamespace(
            request=_Request(),
            config=SimpleNamespace(openapi_v2_location="https://openapi.tidal.com/v2/"),
        )
        album = type(
            "Album",
            (),
            {
                "id": 691734,
                "session": session,
                "tracks": lambda self, limit=1: [type("Track", (), {"id": 691735})()],
            },
        )()
        self.assertEqual(genre_from_tidal_album(album, fetch_tracks=True), "Rock")


class TidalPeakExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_tidal_catalog_stream_probe_cache()

    def test_uses_album_sample_rate_metadata(self) -> None:
        album = SimpleNamespace(sample_rate=96_000, bit_depth=24)
        depth, rate = peak_rate_depth_from_tidal_album(album)
        self.assertEqual(depth, 24)
        self.assertEqual(rate, 96_000)

    def test_uses_stream_probe_when_metadata_has_no_rate(self) -> None:
        album = SimpleNamespace(
            id=145412913,
            audio_quality="LOSSLESS",
            media_metadata_tags=["LOSSLESS", "HIRES_LOSSLESS"],
            get_audio_resolution=lambda: [(24, 88_200)],
        )
        depth, rate = peak_rate_depth_from_tidal_album(album)
        self.assertEqual(rate, 88_200)
        self.assertEqual(depth, 24)

    def test_skips_stream_probe_for_lossy_album(self) -> None:
        album = SimpleNamespace(
            audio_quality="HIGH",
            media_metadata_tags=None,
            get_audio_resolution=lambda: (_ for _ in ()).throw(
                AssertionError("stream probe must not run for lossy"),
            ),
        )
        depth, rate = peak_rate_depth_from_tidal_album(album)
        self.assertIsNone(rate)
        self.assertIsNone(depth)


if __name__ == "__main__":
    unittest.main()
