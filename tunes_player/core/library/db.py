"""SQLite schema and connection for the local library index."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 7

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
    year INTEGER,
    is_synthetic INTEGER NOT NULL DEFAULT 0,
    total_tracks INTEGER,
    genre TEXT,
    release_type_tag TEXT
);

CREATE TABLE IF NOT EXISTS album_art (
    album_id TEXT PRIMARY KEY,
    art_uri TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS play_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    release_id TEXT NOT NULL,
    source TEXT NOT NULL,
    played_at_ns INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tracks_album_id ON tracks(album_id);
CREATE INDEX IF NOT EXISTS idx_tracks_album_artist ON tracks(album_artist);
CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks(title);
CREATE INDEX IF NOT EXISTS idx_play_history_played_at ON play_history(played_at_ns DESC);
CREATE INDEX IF NOT EXISTS idx_play_history_release ON play_history(release_id);
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

_MIGRATION_V5_UNKNOWN_ALBUM = """
UPDATE tracks
SET
    album_id = 'local:release:synthetic:' || id,
    is_synthetic = 1,
    album = title
WHERE trim(album) = ''
   OR album = 'Unknown Album'
   OR lower(trim(album)) = 'unknown album';
"""

_MIGRATION_V6_PLAY_HISTORY = """
CREATE TABLE IF NOT EXISTS play_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    release_id TEXT NOT NULL,
    source TEXT NOT NULL,
    played_at_ns INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_play_history_played_at ON play_history(played_at_ns DESC);
CREATE INDEX IF NOT EXISTS idx_play_history_release ON play_history(release_id);
"""


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row["name"]) for row in rows}


def _add_column_if_missing(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    if column in _table_columns(connection, table):
        return
    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _ensure_repair(connection: sqlite3.Connection) -> None:
    """Idempotent repair for objects missing on mis-initialized DBs."""
    connection.executescript(_MIGRATION_V6_PLAY_HISTORY)
    _add_column_if_missing(connection, "tracks", "release_type_tag", "TEXT")


def _migrate_v5(connection: sqlite3.Connection) -> None:
    _add_column_if_missing(
        connection,
        "tracks",
        "is_synthetic",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(connection, "tracks", "total_tracks", "INTEGER")
    _add_column_if_missing(connection, "tracks", "genre", "TEXT")
    connection.executescript(_MIGRATION_V5_UNKNOWN_ALBUM)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 30000")
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
        _ensure_repair(connection)
        connection.commit()
        return

    stored_version = int(row["value"])
    if stored_version < 2:
        connection.executescript(_MIGRATION_V2)
    if stored_version < 3:
        connection.executescript(_MIGRATION_V3)
    if stored_version < 4:
        connection.executescript(_MIGRATION_V4)
    if stored_version < 5:
        _migrate_v5(connection)
    if stored_version < 6:
        connection.executescript(_MIGRATION_V6_PLAY_HISTORY)
    if stored_version < 7:
        _add_column_if_missing(connection, "tracks", "release_type_tag", "TEXT")
    if stored_version < SCHEMA_VERSION:
        connection.execute(
            "UPDATE meta SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION),),
        )
        connection.commit()
    _ensure_repair(connection)
    connection.commit()
