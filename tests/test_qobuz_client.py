"""Unit tests for Qobuz backend helpers (no network)."""

from __future__ import annotations

import unittest

from tunes_player.core.backends.qobuz import ids as qobuz_ids
from tunes_player.core.backends.qobuz.client import _album_added_ns, sign_get_file_url
from tunes_player.core.backends.qobuz.convert import cover_url, release_from_qobuz
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


if __name__ == "__main__":
    unittest.main()
