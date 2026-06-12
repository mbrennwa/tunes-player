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

    def test_art_uri_map_works_while_store_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "library.db"
            store = LibraryStore(db_path)
            album_id = ids.release_id("Artist", "Album")
            connection = connect(db_path)
            try:
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

            store.close()
            result = store.art_uri_map([album_id])
            self.assertEqual(result[album_id], local_art_uri(album_id))

    def test_release_count_works_while_store_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "library.db"
            store = LibraryStore(db_path)
            connection = connect(db_path)
            try:
                file_id = connection.execute(
                    "INSERT INTO files(path, mtime_ns, size_bytes, indexed_at_ns) VALUES (?, ?, ?, ?)",
                    ("/music/a.flac", 1, 1, 1),
                ).lastrowid
                connection.execute(
                    """
                    INSERT INTO tracks(
                        id, file_id, album_id, title, artist, album_artist, album, is_synthetic
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        "local:file:a",
                        file_id,
                        ids.release_id("Artist", "Album"),
                        "Track",
                        "Artist",
                        "Artist",
                        "Album",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            store.close()
            self.assertEqual(store.release_count(), 1)


if __name__ == "__main__":
    unittest.main()
