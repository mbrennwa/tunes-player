"""Service-level tests for tile-tier release track fetch."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tunes_player.core.config import ConfigManager
from tunes_player.core.models import Release, ReleaseCompleteness, Source, Track
from tunes_player.core.release_quality import (
    QUALITY_FILTER_CD,
    QUALITY_FILTER_HI_RES,
    PlaybackPreference,
)
from tunes_player.core.services import PlayerService


class GetReleaseTracksPlaybackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        config = ConfigManager(Path(self._tmpdir.name) / "config.toml")
        config.load()
        self._service = PlayerService(config=config)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_get_release_tracks_uses_catalog_id_and_tile_tier(self) -> None:
        tile_id = "tidal:album:404893856@cd"
        catalog_id = "tidal:album:404893856"
        tile = Release(
            id=tile_id,
            title="Album",
            artist_name="Artist",
            source=Source.TIDAL,
            track_count=10,
            completeness=ReleaseCompleteness.COMPLETE,
            peak_quality_tier=QUALITY_FILTER_CD,
            available_quality_tiers=frozenset({QUALITY_FILTER_CD}),
            catalog_release_id=catalog_id,
            quality_tier=QUALITY_FILTER_CD,
            catalog_quality_ready=True,
        )
        self._service.cache_release_summary(tile)
        tracks = [
            Track(
                id="tidal:track:1",
                title="One",
                artist_name="Artist",
                album_title="Album",
                source=Source.TIDAL,
            ),
        ]

        with patch.object(
            self._service._tidal,
            "get_release_tracks",
            return_value=tracks,
        ) as get_tracks:
            with patch.object(self._service._tidal, "is_logged_in", return_value=True):
                result = self._service.get_release_tracks(
                    tile_id,
                    playback_preference=PlaybackPreference(QUALITY_FILTER_CD),
                )

        self.assertEqual(result, tracks)
        get_tracks.assert_called_once_with(catalog_id)

    def test_playback_preference_for_release_id_from_tile(self) -> None:
        tile = Release(
            id="qobuz:album:abc@hi_res",
            title="Album",
            artist_name="Artist",
            source=Source.QOBUZ,
            catalog_release_id="qobuz:album:abc",
            quality_tier=QUALITY_FILTER_HI_RES,
            peak_quality_tier=QUALITY_FILTER_HI_RES,
            available_quality_tiers=frozenset({QUALITY_FILTER_HI_RES}),
            catalog_quality_ready=True,
        )
        self._service.cache_release_summary(tile)
        pref = self._service.playback_preference_for_release_id(tile.id)
        self.assertEqual(pref.max_tier, QUALITY_FILTER_HI_RES)


if __name__ == "__main__":
    unittest.main()
