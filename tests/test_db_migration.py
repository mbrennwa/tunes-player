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
            self.assertIn("release_type_tag", columns)

    def test_v7_release_type_tag_from_v6(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "library.db"
            connection = sqlite3.connect(db_path)
            connection.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO meta(key, value) VALUES ('schema_version', '6');
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
                CREATE TABLE play_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id TEXT NOT NULL,
                    release_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    played_at_ns INTEGER NOT NULL
                );
                """
            )
            connection.commit()
            connection.close()

            connect(db_path)
            connection = sqlite3.connect(db_path)
            version = int(
                connection.execute(
                    "SELECT value FROM meta WHERE key = 'schema_version'",
                ).fetchone()[0],
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(tracks)").fetchall()
            }
            connection.close()
            self.assertEqual(version, SCHEMA_VERSION)
            self.assertIn("release_type_tag", columns)

    def test_fresh_connect_creates_play_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "library.db"
            connect(db_path)
            connection = sqlite3.connect(db_path)
            version = int(
                connection.execute(
                    "SELECT value FROM meta WHERE key = 'schema_version'",
                ).fetchone()[0],
            )
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            connection.close()
            self.assertEqual(version, SCHEMA_VERSION)
            self.assertIn("play_history", tables)

    def test_v7_without_play_history_is_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "library.db"
            connection = sqlite3.connect(db_path)
            connection.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO meta(key, value) VALUES ('schema_version', '7');
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
            connection = sqlite3.connect(db_path)
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            connection.close()
            self.assertIn("play_history", tables)

    def test_v6_play_history_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "library.db"
            connection = sqlite3.connect(db_path)
            connection.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO meta(key, value) VALUES ('schema_version', '5');
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
            connection = sqlite3.connect(db_path)
            version = int(
                connection.execute(
                    "SELECT value FROM meta WHERE key = 'schema_version'",
                ).fetchone()[0],
            )
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            connection.close()
            self.assertEqual(version, SCHEMA_VERSION)
            self.assertIn("play_history", tables)

    def test_v8_user_labels_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "library.db"
            connection = sqlite3.connect(db_path)
            connection.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO meta(key, value) VALUES ('schema_version', '7');
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
                CREATE TABLE play_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id TEXT NOT NULL,
                    release_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    played_at_ns INTEGER NOT NULL
                );
                """
            )
            connection.commit()
            connection.close()

            connect(db_path)
            connect(db_path)

            connection = sqlite3.connect(db_path)
            version = int(
                connection.execute(
                    "SELECT value FROM meta WHERE key = 'schema_version'",
                ).fetchone()[0],
            )
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            connection.close()
            self.assertEqual(version, SCHEMA_VERSION)
            self.assertIn("user_labels", tables)
            self.assertIn("release_labels", tables)

    def test_v9_label_sync_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "library.db"
            connection = sqlite3.connect(db_path)
            connection.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO meta(key, value) VALUES ('schema_version', '8');
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
                CREATE TABLE user_labels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    created_at_ns INTEGER NOT NULL
                );
                CREATE TABLE release_labels (
                    release_id TEXT NOT NULL,
                    label_id INTEGER NOT NULL,
                    tagged_at_ns INTEGER NOT NULL,
                    PRIMARY KEY (release_id, label_id)
                );
                """
            )
            connection.commit()
            connection.close()

            connect(db_path)
            connection = sqlite3.connect(db_path)
            version = int(
                connection.execute(
                    "SELECT value FROM meta WHERE key = 'schema_version'",
                ).fetchone()[0],
            )
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(release_labels)").fetchall()
            }
            connection.close()
            self.assertEqual(version, SCHEMA_VERSION)
            self.assertIn("release_label_tombstones", tables)
            self.assertIn("dirty", columns)
            self.assertIn("by_device", columns)

    def test_fresh_connect_creates_user_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "library.db"
            connect(db_path)
            connection = sqlite3.connect(db_path)
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            connection.close()
            self.assertIn("user_labels", tables)
            self.assertIn("release_labels", tables)


if __name__ == "__main__":
    unittest.main()
