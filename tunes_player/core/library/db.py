"""SQLite schema and connection for the local library index."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 4

_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
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

CREATE TABLE IF NOT EXISTS tracks (
    id TEXT PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    album_id TEXT NOT NULL,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    album_artist TEXT NOT NULL,
    album TEXT NOT NULL,
    disc_number INTEGER,
    track_number INTEGER,
    year INTEGER
);

CREATE TABLE IF NOT EXISTS album_art (
    album_id TEXT PRIMARY KEY,
    art_uri TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tracks_album_id ON tracks(album_id);
CREATE INDEX IF NOT EXISTS idx_tracks_album_artist ON tracks(album_artist);
CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks(title);
"""

_MIGRATION_V2 = """
CREATE TABLE IF NOT EXISTS album_art (
    album_id TEXT PRIMARY KEY,
    art_uri TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""

_MIGRATION_V3 = """
ALTER TABLE files ADD COLUMN indexed_at_ns INTEGER NOT NULL DEFAULT 0;
UPDATE files SET indexed_at_ns = mtime_ns WHERE indexed_at_ns = 0;
"""

_MIGRATION_V4 = """
UPDATE files SET indexed_at_ns = 0 WHERE indexed_at_ns = mtime_ns;
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    _migrate(connection)
    return connection


def _migrate(connection: sqlite3.Connection) -> None:
    connection.executescript(_SCHEMA)
    row = connection.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'",
    ).fetchone()
    if row is None:
        connection.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        connection.commit()
        return

    version = int(row["value"])
    if version < 2:
        connection.executescript(_MIGRATION_V2)
        version = 2
    if version < 3:
        connection.executescript(_MIGRATION_V3)
        version = 3
    if version < 4:
        connection.executescript(_MIGRATION_V4)
        connection.execute(
            "UPDATE meta SET value = ? WHERE key = 'schema_version'",
            ("4",),
        )
        connection.commit()
