"""Tests for album art cache maintenance."""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tunes_player.core.art import find_cached_art_path, local_art_uri
from tunes_player.core.library import ids
from tunes_player.core.library.art_cache import (
    backfill_missing_album_art,
    maintain_album_art,
    repair_stale_album_art,
    upsert_album_art,
)
from tunes_player.core.library.db import connect


def _seed_track(
    connection: sqlite3.Connection,
    *,
    path: str,
    album_id: str,
    album: str = "Album",
    album_artist: str = "Artist",
) -> None:
    connection.execute(
        """
        INSERT INTO files(path, mtime_ns, size_bytes, indexed_at_ns, codec)
        VALUES (?, 1, 1, 1, 'flac')
        """,
        (path,),
    )
    file_id = connection.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()[0]
    connection.execute(
        """
        INSERT INTO tracks(
            id, file_id, album_id, title, artist, album_artist, album, is_synthetic
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (ids.track_id(path), file_id, album_id, "Track", "Artist", album_artist, album),
    )


class ArtCacheMaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._data_dir = Path(self._tmp.name)
        self._db_path = self._data_dir / "library.db"
        self._connection = connect(self._db_path)

    def tearDown(self) -> None:
        self._connection.close()

    def test_repair_stale_album_art_restores_missing_cache_file(self) -> None:
        album_id = ids.release_id("Artist", "Album")
        path = str((self._data_dir / "track.flac").resolve())
        _seed_track(self._connection, path=path, album_id=album_id)
        self._connection.execute(
            """
            INSERT INTO album_art(album_id, art_uri, mime_type, updated_at)
            VALUES (?, ?, 'image/jpeg', 1)
            """,
            (album_id, local_art_uri(album_id)),
        )
        self._connection.commit()

        with patch(
            "tunes_player.core.library.art_cache.extract_embedded_art",
            return_value=(b"jpeg-bytes", "image/jpeg"),
        ):
            repaired = repair_stale_album_art(self._connection, data_dir=self._data_dir)

        self.assertEqual(repaired, 1)
        self.assertIsNotNone(find_cached_art_path(self._data_dir, album_id))

    def test_repair_stale_album_art_drops_row_when_no_embedded_art(self) -> None:
        album_id = ids.release_id("Artist", "Album")
        path = str((self._data_dir / "track.flac").resolve())
        _seed_track(self._connection, path=path, album_id=album_id)
        self._connection.execute(
            """
            INSERT INTO album_art(album_id, art_uri, mime_type, updated_at)
            VALUES (?, ?, 'image/jpeg', 1)
            """,
            (album_id, local_art_uri(album_id)),
        )
        self._connection.commit()

        with patch(
            "tunes_player.core.library.art_cache.extract_embedded_art",
            return_value=None,
        ):
            repaired = repair_stale_album_art(self._connection, data_dir=self._data_dir)

        self.assertEqual(repaired, 0)
        row = self._connection.execute(
            "SELECT 1 FROM album_art WHERE album_id = ?",
            (album_id,),
        ).fetchone()
        self.assertIsNone(row)

    def test_backfill_missing_album_art_indexes_new_release(self) -> None:
        album_id = ids.release_id("Artist", "Album")
        path = str((self._data_dir / "track.flac").resolve())
        _seed_track(self._connection, path=path, album_id=album_id)
        self._connection.commit()

        with patch(
            "tunes_player.core.library.art_cache.extract_embedded_art",
            return_value=(b"jpeg-bytes", "image/jpeg"),
        ):
            added = backfill_missing_album_art(self._connection, data_dir=self._data_dir)

        self.assertEqual(added, 1)
        self.assertIsNotNone(find_cached_art_path(self._data_dir, album_id))

    def test_maintain_album_art_runs_repair_then_backfill(self) -> None:
        stale_id = ids.release_id("Artist", "Stale")
        missing_id = ids.release_id("Artist", "Missing")
        stale_path = str((self._data_dir / "stale.flac").resolve())
        missing_path = str((self._data_dir / "missing.flac").resolve())
        _seed_track(self._connection, path=stale_path, album_id=stale_id, album="Stale")
        _seed_track(self._connection, path=missing_path, album_id=missing_id, album="Missing")
        self._connection.execute(
            """
            INSERT INTO album_art(album_id, art_uri, mime_type, updated_at)
            VALUES (?, ?, 'image/jpeg', 1)
            """,
            (stale_id, local_art_uri(stale_id)),
        )
        self._connection.commit()

        def fake_extract(path: Path) -> tuple[bytes, str] | None:
            return (b"jpeg-bytes", "image/jpeg")

        with patch(
            "tunes_player.core.library.art_cache.extract_embedded_art",
            side_effect=fake_extract,
        ):
            added, repaired = maintain_album_art(self._connection, data_dir=self._data_dir)

        self.assertEqual(repaired, 1)
        self.assertEqual(added, 1)
        self.assertIsNotNone(find_cached_art_path(self._data_dir, stale_id))
        self.assertIsNotNone(find_cached_art_path(self._data_dir, missing_id))

    def test_upsert_uses_track_album_id_for_cache_key(self) -> None:
        album_id = ids.synthetic_release_id(ids.track_id("/music/one.flac"))
        upsert_album_art(
            self._connection,
            data_dir=self._data_dir,
            album_id=album_id,
            art_data=b"png-bytes",
            mime_type="image/png",
        )
        self.assertIsNotNone(find_cached_art_path(self._data_dir, album_id))


if __name__ == "__main__":
    unittest.main()
