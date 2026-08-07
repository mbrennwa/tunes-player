"""Unit tests for GTK util helpers that do not need a display."""

from __future__ import annotations

import unittest

from tunes_player.core.models import Source, Track
from tunes_player.ui.gtk.util import tracks_have_mixed_artists


def _track(**kwargs: object) -> Track:
    values = {
        "id": "local:1",
        "title": "Song",
        "artist_name": "Artist",
        "release_title": "Album",
        "source": Source.LOCAL,
        "track_number": 1,
    }
    values.update(kwargs)
    return Track(**values)  # type: ignore[arg-type]


class TestTracksHaveMixedArtists(unittest.TestCase):
    def test_same_artists(self) -> None:
        tracks = [
            _track(id="1", artist_name="Artist"),
            _track(id="2", artist_name="Artist"),
        ]
        self.assertFalse(tracks_have_mixed_artists(tracks))

    def test_mixed_artists(self) -> None:
        tracks = [
            _track(id="1", artist_name="A"),
            _track(id="2", artist_name="B"),
        ]
        self.assertTrue(tracks_have_mixed_artists(tracks))

    def test_empty_list(self) -> None:
        self.assertFalse(tracks_have_mixed_artists([]))

    def test_blank_artists(self) -> None:
        tracks = [
            _track(id="1", artist_name=""),
            _track(id="2", artist_name=""),
        ]
        self.assertFalse(tracks_have_mixed_artists(tracks))

    def test_single_track(self) -> None:
        self.assertFalse(tracks_have_mixed_artists([_track()]))


if __name__ == "__main__":
    unittest.main()
