"""Regression tests for release grid artwork refresh during local scans."""

from __future__ import annotations

import unittest

from tunes_player.core.art import local_art_uri
from tunes_player.core.models import Release, Source
from tunes_player.ui.gtk.views import ReleaseTileGrid, _release_card


class ReleaseGridArtRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.grid = ReleaseTileGrid(inner_width_fn=lambda: 800)

    def test_streaming_art_preserved_when_map_has_none(self) -> None:
        streaming_uri = "https://example.com/cover.jpg"
        release = Release(
            id="qobuz:album:42",
            title="Streaming Album",
            artist_name="Artist",
            source=Source.QOBUZ,
            art_uri=streaming_uri,
        )
        card = _release_card(release, on_play=lambda: None, art_loader=None, load_art=False)
        self.grid._cards = [card]

        self.grid.refresh_card_art_uris({release.id: None})

        self.assertEqual(getattr(card, "_tunes_art_uri"), streaming_uri)

    def test_local_art_updated_from_map(self) -> None:
        release = Release(
            id="local:album:abc123",
            title="Local Album",
            artist_name="Artist",
            source=Source.LOCAL,
            art_uri=None,
        )
        card = _release_card(release, on_play=lambda: None, art_loader=None, load_art=False)
        self.grid._cards = [card]
        new_uri = local_art_uri(release.id)

        self.grid.refresh_card_art_uris({release.id: new_uri})

        self.assertEqual(getattr(card, "_tunes_art_uri"), new_uri)


if __name__ == "__main__":
    unittest.main()
