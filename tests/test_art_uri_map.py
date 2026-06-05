"""Tests for release art URI lookups."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tunes_player.core.art import local_art_uri
from tunes_player.core.library import ids
from tunes_player.core.library.db import connect
from tunes_player.core.library.store import LibraryStore


class ArtUriMapTests(unittest.TestCase):
    def test_art_uri_map_includes_missing_entries(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "library.db"
            store = LibraryStore(db_path)
            connection = connect(db_path)
            try:
                album_id = ids.release_id("Artist", "Album")
                connection.execute(
                    """
                    INSERT INTO album_art(album_id, art_uri, mime_type, updated_at)
                    VALUES (?, ?, 'image/jpeg', 1)
                    """,
                    (album_id, local_art_uri(album_id)),
                )
                connection.commit()
            finally:
                connection.close()

            missing_id = ids.release_id("Artist", "Other")
            result = store.art_uri_map([album_id, missing_id])
            self.assertEqual(result[album_id], local_art_uri(album_id))
            self.assertIsNone(result[missing_id])


if __name__ == "__main__":
    unittest.main()
