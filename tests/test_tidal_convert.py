"""Tests for TIDAL release conversion."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from tunes_player.core.backends.tidal.convert import (
    _resolve_tidal_release_type,
    release_from_tidal,
)
from tunes_player.core.models import ReleaseType


class _FakeSession:
    def __init__(self, *, full_type: str | None = "EP") -> None:
        self._full_type = full_type

    def album(self, album_id: object) -> SimpleNamespace:
        return SimpleNamespace(
            id=album_id,
            type=self._full_type,
            name="Full Title",
            num_tracks=5,
            artists=[SimpleNamespace(name="Artist")],
            release_date=None,
        )

    def image(self, *_args: object, **_kwargs: object) -> None:
        return None


class TidalConvertTests(unittest.TestCase):
    def test_sparse_album_fetches_type_for_ep(self) -> None:
        sparse = SimpleNamespace(id=999, type=None)
        session = _FakeSession(full_type="EP")
        self.assertEqual(_resolve_tidal_release_type(session, sparse), "EP")

    def test_release_from_sparse_search_album_is_ep(self) -> None:
        sparse = SimpleNamespace(
            id=999,
            type=None,
            name="Who the Fuck Are Arctic Monkeys?",
            num_tracks=5,
            artists=[SimpleNamespace(name="Arctic Monkeys")],
            release_date=None,
        )
        session = _FakeSession(full_type="EP")
        release = release_from_tidal(session, sparse)
        self.assertEqual(release.release_type, ReleaseType.EP)

    def test_full_album_type_used_without_extra_fetch(self) -> None:
        sparse = SimpleNamespace(
            id=1,
            type="EP",
            name="EP",
            num_tracks=4,
            artists=[SimpleNamespace(name="Artist")],
            release_date=None,
        )
        session = _FakeSession(full_type="ALBUM")

        class _SpySession(_FakeSession):
            def album(self, album_id: object) -> SimpleNamespace:
                raise AssertionError("should not refetch when type is set")

        release = release_from_tidal(_SpySession(), sparse)
        self.assertEqual(release.release_type, ReleaseType.EP)


if __name__ == "__main__":
    unittest.main()
