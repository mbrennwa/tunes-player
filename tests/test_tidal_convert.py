"""Tests for TIDAL release conversion."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from tunes_player.core.backends.tidal.convert import (
    _resolve_tidal_release_type,
    release_from_tidal,
)
from tunes_player.core.models import ReleaseType
from tunes_player.core.release_quality import (
    QUALITY_FILTER_CD,
    QUALITY_FILTER_COMPRESSED,
    QUALITY_FILTER_HI_RES,
)


class _FakeSession:
    def __init__(
        self,
        *,
        full_type: str | None = "EP",
        tracks: list[object] | None = None,
    ) -> None:
        self._full_type = full_type
        self._tracks = tracks or [
            SimpleNamespace(
                audio_quality="LOSSLESS",
                media_metadata_tags=None,
            ),
        ]

    def album(self, album_id: object) -> SimpleNamespace:
        return SimpleNamespace(
            id=album_id,
            type=self._full_type,
            name="Full Title",
            num_tracks=5,
            artists=[SimpleNamespace(name="Artist")],
            release_date=None,
            tracks=lambda: self._tracks,
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

    def test_release_peak_quality_from_album_tracks(self) -> None:
        sparse = SimpleNamespace(
            id=42,
            type="ALBUM",
            name="Hi-Fi",
            num_tracks=2,
            artists=[SimpleNamespace(name="Artist")],
            release_date=None,
            tracks=lambda: [
                SimpleNamespace(audio_quality="HIGH", media_metadata_tags=None),
                SimpleNamespace(
                    audio_quality="HI_RES_LOSSLESS",
                    media_metadata_tags=["HIRES_LOSSLESS"],
                ),
            ],
        )
        session = _FakeSession()
        release = release_from_tidal(session, sparse, resolve_peak_quality=True)
        self.assertEqual(release.peak_quality_tier, QUALITY_FILTER_HI_RES)

    def test_release_from_tidal_skips_track_fetch_by_default(self) -> None:
        sparse = SimpleNamespace(
            id=42,
            type="ALBUM",
            name="Hi-Fi",
            num_tracks=2,
            artists=[SimpleNamespace(name="Artist")],
            release_date=None,
            tracks=lambda: (_ for _ in ()).throw(
                AssertionError("tracks() must not run for grid conversion"),
            ),
        )
        session = _FakeSession()
        release = release_from_tidal(session, sparse)
        self.assertEqual(release.peak_quality_tier, QUALITY_FILTER_COMPRESSED)

    def test_release_peak_quality_fetches_sparse_album_tracks(self) -> None:
        sparse = SimpleNamespace(
            id=77,
            type="ALBUM",
            name="Sparse",
            num_tracks=1,
            artists=[SimpleNamespace(name="Artist")],
            release_date=None,
        )
        session = _FakeSession(
            tracks=[
                SimpleNamespace(audio_quality="HIGH", media_metadata_tags=None),
            ],
        )
        release = release_from_tidal(session, sparse, resolve_peak_quality=True)
        self.assertEqual(release.peak_quality_tier, QUALITY_FILTER_COMPRESSED)


if __name__ == "__main__":
    unittest.main()
