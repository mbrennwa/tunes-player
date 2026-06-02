"""Database migration tests."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tunes_player.core.library.db import SCHEMA_VERSION, connect


class DbMigrationTests(unittest.TestCase):
    def test_v5_migration_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "library.db"
            connection = sqlite3.connect(db_path)
            connection.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO meta(key, value) VALUES ('schema_version', '4');
                CREATE TABLE files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    mtime_ns INTEGER NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    indexed_at_ns INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE tracks (
                    id TEXT PRIMARY KEY,
                    file_id INTEGER NOT NULL,
                    album_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    artist TEXT NOT NULL,
                    album_artist TEXT NOT NULL,
                    album TEXT NOT NULL,
                    is_synthetic INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            connection.commit()
            connection.close()

            connect(db_path)
            connect(db_path)

            connection = sqlite3.connect(db_path)
            version = connection.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'",
            ).fetchone()[0]
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(tracks)").fetchall()
            }
            connection.close()

            self.assertEqual(int(version), SCHEMA_VERSION)
            self.assertIn("is_synthetic", columns)
            self.assertIn("total_tracks", columns)
            self.assertIn("genre", columns)


if __name__ == "__main__":
    unittest.main()
