"""Tests for play history and local suggestion queries."""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from tunes_player.core.library.db import SCHEMA_VERSION, connect
from tunes_player.core.library.store import LibraryStore
from tunes_player.core.models import Source


class SuggestionsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "library.db"
        connection = sqlite3.connect(self._db_path)
        connection.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO meta(key, value) VALUES ('schema_version', '5');
            CREATE TABLE files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                mtime_ns INTEGER NOT NULL,
                size_bytes INTEGER NOT NULL,
                indexed_at_ns INTEGER NOT NULL DEFAULT 0,
                codec TEXT,
                duration_sec REAL,
                sample_rate INTEGER,
                bit_depth INTEGER,
                channels INTEGER
            );
            CREATE TABLE tracks (
                id TEXT PRIMARY KEY,
                file_id INTEGER NOT NULL,
                album_id TEXT NOT NULL,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                album_artist TEXT NOT NULL,
                album TEXT NOT NULL,
                disc_number INTEGER,
                track_number INTEGER,
                year INTEGER,
                is_synthetic INTEGER NOT NULL DEFAULT 0,
                total_tracks INTEGER,
                genre TEXT
            );
            CREATE TABLE album_art (
                album_id TEXT PRIMARY KEY,
                art_uri TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );
            """
        )
        connection.commit()
        connection.close()
        connect(self._db_path)
        self._store = LibraryStore(self._db_path)

    def tearDown(self) -> None:
        self._store.close()
        self._tmp.cleanup()

    def _insert_local_track(
        self,
        *,
        track_id: str,
        album_id: str,
        genre: str | None = None,
    ) -> None:
        conn = self._store.connection
        conn.execute(
            """
            INSERT INTO files(path, mtime_ns, size_bytes, indexed_at_ns)
            VALUES (?, 1, 1, 1)
            """,
            (f"/music/{track_id}.flac",),
        )
        file_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO tracks(
                id, file_id, album_id, title, artist, album_artist, album, genre
            ) VALUES (?, ?, ?, ?, 'Artist', 'Artist', 'Album', ?)
            """,
            (track_id, file_id, album_id, track_id, genre),
        )
        conn.commit()

    def test_v6_play_history_table(self) -> None:
        version = self._store.connection.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'",
        ).fetchone()[0]
        self.assertEqual(int(version), SCHEMA_VERSION)
        tables = {
            row[0]
            for row in self._store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        }
        self.assertIn("play_history", tables)

    def test_continue_listening_orders_by_recency(self) -> None:
        self._insert_local_track(track_id="local:track:1", album_id="local:album:a")
        self._insert_local_track(track_id="local:track:2", album_id="local:album:b")
        self._store.record_play(
            track_id="local:track:1",
            release_id="local:album:a",
            source=Source.LOCAL.value,
            played_at_ns=100,
        )
        self._store.record_play(
            track_id="local:track:2",
            release_id="local:album:b",
            source=Source.LOCAL.value,
            played_at_ns=200,
        )
        entries = self._store.list_continue_listening_entries(limit=10)
        self.assertEqual([release_id for release_id, _ in entries], ["local:album:b", "local:album:a"])

    def test_rediscover_uses_genre_from_recent_plays(self) -> None:
        self._insert_local_track(
            track_id="local:track:old",
            album_id="local:album:old",
            genre="Jazz",
        )
        self._insert_local_track(
            track_id="local:track:new",
            album_id="local:album:new",
            genre="Jazz",
        )
        now = time.time_ns()
        old_play = now - int(20 * 365.25 * 86_400 * 1_000_000_000)
        self._store.record_play(
            track_id="local:track:old",
            release_id="local:album:old",
            source=Source.LOCAL.value,
            played_at_ns=old_play,
        )
        self._store.record_play(
            track_id="local:track:new",
            release_id="local:album:new",
            source=Source.LOCAL.value,
            played_at_ns=now,
        )
        items = self._store.list_rediscover_items(
            limit=10,
            idle_months=18,
            recent_genre_days=3650,
        )
        ids = {item.release.id for item in items}
        self.assertIn("local:album:old", ids)
        self.assertNotIn("local:album:new", ids)


if __name__ == "__main__":
    unittest.main()
