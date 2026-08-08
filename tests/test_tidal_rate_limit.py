"""Tests for TIDAL 429 retry on album metadata and stream fetches."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tidalapi.exceptions import TooManyRequests

from tunes_player.core.backends.tidal.client import (
    TidalClient,
    TidalUnavailableError,
    _RATE_LIMIT_MESSAGE,
    _RATE_LIMIT_RETRY_ATTEMPTS,
    _call_with_rate_limit_retry,
)
from tunes_player.core.config import ConfigManager
from tunes_player.core.services import PlayerService


class CallWithRateLimitRetryTests(unittest.TestCase):
    def test_retries_then_succeeds(self) -> None:
        calls = {"n": 0}

        def operation() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise TooManyRequests("slow down")
            return "ok"

        with patch(
            "tunes_player.core.backends.tidal.client.time.sleep",
            return_value=None,
        ) as sleep:
            result = _call_with_rate_limit_retry(operation, label="album 1")

        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(sleep.call_count, 2)

    def test_exhausted_retries_raise_unavailable(self) -> None:
        def operation() -> str:
            raise TooManyRequests("Album unavailable")

        with patch(
            "tunes_player.core.backends.tidal.client.time.sleep",
            return_value=None,
        ) as sleep:
            with self.assertRaises(TidalUnavailableError) as ctx:
                _call_with_rate_limit_retry(operation, label="album 9")

        self.assertEqual(str(ctx.exception), _RATE_LIMIT_MESSAGE)
        self.assertEqual(sleep.call_count, _RATE_LIMIT_RETRY_ATTEMPTS - 1)


class AlbumRateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        session_file = Path(self._tmpdir.name) / "tidal-session.json"
        session_file.write_text("{}", encoding="utf-8")
        self._client = TidalClient(session_file)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_album_or_none_retries_rate_limit(self) -> None:
        album = SimpleNamespace(id=42)
        session = MagicMock()
        session.album.side_effect = [
            TooManyRequests("Album unavailable"),
            album,
        ]

        with (
            patch.object(self._client, "_require_login", return_value=session),
            patch(
                "tunes_player.core.backends.tidal.client.time.sleep",
                return_value=None,
            ),
        ):
            result = self._client._album_or_none(session, "42")

        self.assertIs(result, album)
        self.assertEqual(session.album.call_count, 2)

    def test_get_release_tracks_maps_exhausted_429(self) -> None:
        album = MagicMock()
        album.tracks.side_effect = TooManyRequests("Album unavailable")
        session = MagicMock()

        with (
            patch.object(self._client, "_require_login", return_value=session),
            patch.object(self._client, "_album_or_none", return_value=album),
            patch(
                "tunes_player.core.backends.tidal.client.time.sleep",
                return_value=None,
            ),
        ):
            with self.assertRaises(TidalUnavailableError) as ctx:
                self._client.get_release_tracks("tidal:album:99")

        self.assertEqual(str(ctx.exception), _RATE_LIMIT_MESSAGE)
        self.assertEqual(album.tracks.call_count, _RATE_LIMIT_RETRY_ATTEMPTS)


class ServiceGetReleaseTracksUnavailableTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        config = ConfigManager(Path(self._tmpdir.name) / "config.toml")
        config.load()
        self._service = PlayerService(config=config)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_get_release_tracks_rethrows_tidal_unavailable(self) -> None:
        with (
            patch.object(self._service._tidal, "is_logged_in", return_value=True),
            patch.object(
                self._service._tidal,
                "get_release_tracks",
                side_effect=TidalUnavailableError(_RATE_LIMIT_MESSAGE),
            ),
        ):
            with self.assertRaises(TidalUnavailableError) as ctx:
                self._service.get_release_tracks("tidal:album:404893856")

        self.assertEqual(str(ctx.exception), _RATE_LIMIT_MESSAGE)


if __name__ == "__main__":
    unittest.main()
