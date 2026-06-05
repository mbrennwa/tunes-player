"""Unit tests for Qobuz backend helpers (no network)."""

from __future__ import annotations

import unittest

from tunes_player.core.backends.qobuz import ids as qobuz_ids
from tunes_player.core.backends.qobuz.client import (
    _SUGGESTION_FEATURE_TYPES,
    _album_added_ns,
    sign_get_file_url,
)
from tunes_player.core.backends.qobuz.client import QobuzClient
from tunes_player.core.backends.qobuz.convert import cover_url, release_from_qobuz
from tunes_player.core.release_quality import (
    QUALITY_FILTER_CD,
    QUALITY_FILTER_HI_RES,
)
from tunes_player.core.models import Source


class TestQobuzIds(unittest.TestCase):
    def test_track_id_roundtrip(self) -> None:
        self.assertEqual(qobuz_ids.track_id(24393138), "qobuz:track:24393138")
        self.assertEqual(qobuz_ids.parse_prefixed_id("qobuz:track:24393138", "track"), "24393138")

    def test_album_id(self) -> None:
        self.assertEqual(qobuz_ids.album_id("0060254735180"), "qobuz:album:0060254735180")

    def test_parse_wrong_prefix(self) -> None:
        self.assertIsNone(qobuz_ids.parse_prefixed_id("tidal:track:1", "track"))


class TestSignGetFileUrlReal(unittest.TestCase):
    def test_matches_streamrip_pattern(self) -> None:
        import hashlib

        track_id = "19512574"
        format_id = 27
        ts = 1234567890.0
        secret = "testsecret"
        raw = f"trackgetFileUrlformat_id{format_id}intentstreamtrack_id{track_id}{ts}{secret}"
        expected = hashlib.md5(raw.encode("utf-8")).hexdigest()
        self.assertEqual(
            sign_get_file_url(
                track_id=track_id,
                format_id=format_id,
                request_ts=ts,
                app_secret=secret,
            ),
            expected,
        )


class TestCoverUrl(unittest.TestCase):
    def test_hash_to_static_url(self) -> None:
        image = "abcdefghijkl"
        url = cover_url(image)
        self.assertEqual(
            url,
            "https://static.qobuz.com/images/covers/ab/cd/ef/abcdefghijkl_org.jpg",
        )

    def test_dict_large_url(self) -> None:
        url = cover_url({"large": "https://static.qobuz.com/foo.jpg"})
        self.assertEqual(url, "https://static.qobuz.com/foo.jpg")


class TestAlbumAddedNs(unittest.TestCase):
    def test_release_date_stream(self) -> None:
        ns = _album_added_ns({"release_date_stream": "2026-03-15"})
        self.assertGreater(ns, 0)

    def test_missing_date_uses_now(self) -> None:
        import time

        before = time.time_ns()
        ns = _album_added_ns({})
        after = time.time_ns()
        self.assertGreaterEqual(ns, before)
        self.assertLessEqual(ns, after)


class TestQobuzSuggestionFeatureTypes(unittest.TestCase):
    def test_suggestion_types_disjoint_from_new_releases(self) -> None:
        from tunes_player.core.backends.qobuz.client import _NEW_RELEASE_FEATURE_TYPES

        overlap = set(_SUGGESTION_FEATURE_TYPES) & set(_NEW_RELEASE_FEATURE_TYPES)
        self.assertEqual(overlap, set())


class TestQobuzListSuggestionItems(unittest.TestCase):
    def test_flattens_featured_albums(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "qobuz.json"
            client = QobuzClient(session, app_id="1", app_secret="secret")
            client._user_auth_token = "token"  # noqa: SLF001

            album = {
                "id": "99",
                "title": "Editor Pick",
                "artist": {"name": "Band"},
                "tracks_count": 1,
            }

            def fake_api_get(endpoint: str, params: dict | None = None, **kwargs: object):
                assert endpoint == "album/getFeatured"
                feature_type = (params or {}).get("type")
                if feature_type in _SUGGESTION_FEATURE_TYPES:
                    return {"albums": {"items": [album]}}
                return {"albums": {"items": []}}

            with patch.object(client, "_api_get", side_effect=fake_api_get):
                items = client.list_suggestion_items(limit=50)

            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].release.id, "qobuz:album:99")


class TestReleaseFromQobuz(unittest.TestCase):
    def test_minimal_album(self) -> None:
        album = {
            "id": "12345",
            "title": "Test Album",
            "artist": {"id": 1, "name": "Artist One"},
            "tracks_count": 10,
            "tracks": {"total": 10, "items": []},
            "image": "abc123456789",
            "release_date_stream": "2024-06-15",
        }
        release = release_from_qobuz(album)
        self.assertEqual(release.id, "qobuz:album:12345")
        self.assertEqual(release.title, "Test Album")
        self.assertEqual(release.artist_name, "Artist One")
        self.assertEqual(release.source, Source.QOBUZ)
        self.assertEqual(release.year, 2024)
        self.assertIsNotNone(release.art_uri)
        self.assertEqual(release.release_type.value, "album")

    def test_product_type_ep(self) -> None:
        album = {
            "id": "ep1",
            "title": "Short Run",
            "artist": {"name": "Band"},
            "tracks_count": 4,
            "product_type": "ep",
        }
        release = release_from_qobuz(album)
        self.assertEqual(release.release_type.value, "ep")

    def test_product_type_single(self) -> None:
        album = {
            "id": "s1",
            "title": "Hit",
            "artist": {"name": "Band"},
            "tracks_count": 1,
            "product_type": "single",
        }
        release = release_from_qobuz(album)
        self.assertEqual(release.release_type.value, "single")

    def test_peak_quality_cd(self) -> None:
        album = {
            "id": "cd1",
            "title": "CD Album",
            "artist": {"name": "Band"},
            "tracks_count": 8,
            "maximum_bit_depth": 16,
            "maximum_sampling_rate": 44100,
            "hires": False,
        }
        release = release_from_qobuz(album)
        self.assertEqual(release.peak_quality_tier, QUALITY_FILTER_CD)

    def test_peak_quality_hi_res(self) -> None:
        album = {
            "id": "hr1",
            "title": "Hi-Res Album",
            "artist": {"name": "Band"},
            "tracks_count": 8,
            "maximum_bit_depth": 24,
            "maximum_sampling_rate": 96000,
            "hires": True,
        }
        release = release_from_qobuz(album)
        self.assertEqual(release.peak_quality_tier, QUALITY_FILTER_HI_RES)

    def test_peak_quality_hi_res_khz_sample_rate(self) -> None:
        album = {
            "id": "hr2",
            "title": "192 Album",
            "artist": {"name": "Band"},
            "tracks_count": 8,
            "maximum_bit_depth": 24,
            "maximum_sampling_rate": 192,
            "hires": False,
        }
        release = release_from_qobuz(album)
        self.assertEqual(release.peak_quality_tier, QUALITY_FILTER_HI_RES)


if __name__ == "__main__":
    unittest.main()
