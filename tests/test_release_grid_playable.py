"""Tests for release grid play-button sensitivity."""

from __future__ import annotations

import unittest

from tunes_player.core.models import Release, Source
from tunes_player.ui.gtk.views import _release_grid_playable


class ReleaseGridPlayableTests(unittest.TestCase):
    def test_local_requires_tracks(self) -> None:
        release = Release(
            id="local:1",
            title="Empty",
            artist_name="Artist",
            source=Source.LOCAL,
            track_count=0,
        )
        self.assertFalse(_release_grid_playable(release))

    def test_local_with_tracks(self) -> None:
        release = Release(
            id="local:1",
            title="Album",
            artist_name="Artist",
            source=Source.LOCAL,
            track_count=3,
        )
        self.assertTrue(_release_grid_playable(release))

    def test_streaming_sparse_metadata_still_playable(self) -> None:
        release = Release(
            id="tidal:album:1",
            title="Sparse",
            artist_name="Artist",
            source=Source.TIDAL,
            track_count=0,
        )
        self.assertTrue(_release_grid_playable(release))


if __name__ == "__main__":
    unittest.main()
