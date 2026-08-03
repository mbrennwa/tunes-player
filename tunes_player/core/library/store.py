"""Read models from the local library database."""

from __future__ import annotations

import functools
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tunes_player.core.home import (
    SUGGESTIONS_LOCAL_CONTINUE_LIMIT,
    SUGGESTIONS_LOCAL_REDISCOVER_LIMIT,
    SUGGESTIONS_RECENT_GENRE_DAYS,
    SUGGESTIONS_REDISCOVER_IDLE_MONTHS,
    RecentlyAddedItem,
)
from tunes_player.core.library.db import connect, with_db_retry
from tunes_player.core.library.release_logic import infer_release_metadata
from tunes_player.core.models import Release, Source, Track

_RELEASE_GROUP_SELECT = """
    SELECT
        t.album_id AS release_id,
        t.album,
        t.album_artist,
        MIN(t.year) AS year,
        COUNT(*) AS track_count,
        MAX(t.is_synthetic) AS is_synthetic,
        MAX(t.total_tracks) AS total_tracks_tag,
        MAX(t.track_number) AS max_track_number,
        MAX(t.release_type_tag) AS release_type_tag,
        MIN(t.genre) AS genre,
        SUM(f.duration_sec) AS duration_sec,
        MAX(f.bit_depth) AS max_bit_depth,
        MAX(f.sample_rate) AS max_sample_rate,
        MAX(
            CASE
                WHEN lower(f.codec) IN ('flac', 'alac', 'wav', 'aiff', 'aif') THEN 1
                ELSE 0
            END
        ) AS has_lossless,
        MAX(
            CASE
                WHEN lower(f.codec) IN ('mp3', 'aac', 'vorbis', 'ogg') THEN 1
                ELSE 0
            END
        ) AS has_lossy
    FROM tracks t
    JOIN files f ON f.id = t.file_id
"""


@dataclass(frozen=True, slots=True)
class FileMetadata:
    path: str
    codec: str | None
    duration_sec: float | None
    sample_rate: int | None
    bit_depth: int | None
    channels: int | None


def _locked_db(method):
    @functools.wraps(method)
    def wrapper(self: LibraryStore, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class LibraryStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.RLock()
        self._write_connection: sqlite3.Connection | None = connect(db_path)
        self._read_connection: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        with self._lock:
            return self._db_connection()

    def _db_connection(self) -> sqlite3.Connection:
        if self._write_connection is not None:
            return self._write_connection
        if self._read_connection is not None:
            return self._read_connection
        raise RuntimeError("library store is closed")

    def _is_closed(self) -> bool:
        return self._write_connection is None and self._read_connection is None

    def _with_connection(self, fn: Callable[[sqlite3.Connection], object]):
        if not self._is_closed():
            return fn(self._db_connection())
        connection = connect(self._db_path)
        try:
            return fn(connection)
        finally:
            connection.close()

    @_locked_db
    def close(self) -> None:
        """Release DB connections while a background scan runs."""
        if self._write_connection is not None:
            try:
                self._write_connection.commit()
            except sqlite3.Error:
                pass
            self._write_connection.close()
            self._write_connection = None
        if self._read_connection is not None:
            self._read_connection.close()
            self._read_connection = None

    @_locked_db
    def purge_files_outside_roots(
        self,
        roots: list[str],
        *,
        data_dir: Path,
    ) -> int:
        """Remove indexed files not under *roots* using this store's write connection."""
        from tunes_player.core.library.art_cache import prune_orphan_album_art
        from tunes_player.core.library.scanner import LibraryScanner

        if self._write_connection is None:
            self.reconnect()
        connection = self._write_connection
        if connection is None:
            raise RuntimeError("library store write connection unavailable")

        def attempt() -> int:
            try:
                connection.execute("BEGIN IMMEDIATE")
                removed = LibraryScanner.purge_files_outside_roots(connection, roots)
                prune_orphan_album_art(connection, data_dir=data_dir)
                connection.commit()
                return removed
            except Exception:
                connection.rollback()
                raise

        return with_db_retry(attempt, on_locked=lambda _exc, _attempt: connection.rollback())

    @_locked_db
    def reconnect(self) -> None:
        """Reopen the write connection (call after a scan from another connection)."""
        if self._read_connection is not None:
            self._read_connection.close()
            self._read_connection = None
        if self._write_connection is not None:
            self._write_connection.close()
        self._write_connection = connect(self._db_path)
        self._prune_release_label_tables(self._write_connection)
        self._write_connection.commit()

    @_locked_db
    def track_count(self) -> int:
        def query(connection: sqlite3.Connection) -> int:
            row = connection.execute("SELECT COUNT(*) AS count FROM tracks").fetchone()
            return int(row["count"])

        return int(self._with_connection(query))

    @_locked_db
    def release_count(self) -> int:
        def query(connection: sqlite3.Connection) -> int:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count FROM (
                    SELECT 1
                    FROM tracks
                    GROUP BY album_id, album, album_artist
                )
                """,
            ).fetchone()
            return int(row["count"])

        return int(self._with_connection(query))

    @_locked_db
    def count_files_under_folder(self, folder: str) -> int:
        root = str(Path(folder).expanduser().resolve())

        def query(connection: sqlite3.Connection) -> int:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM files
                WHERE path = ? OR path LIKE ?
                """,
                (root, root + os.sep + "%"),
            ).fetchone()
            return int(row["count"])

        return int(self._with_connection(query))

    @_locked_db
    def list_releases(self) -> list[Release]:
        def query(connection: sqlite3.Connection) -> list[Release]:
            rows = connection.execute(
                f"""
                {_RELEASE_GROUP_SELECT}
                GROUP BY t.album_id, t.album, t.album_artist
                ORDER BY t.album_artist COLLATE NOCASE, t.album COLLATE NOCASE
                """,
            ).fetchall()
            return self._rows_to_releases(rows, connection=connection)

        return self._with_connection(query)

    @_locked_db
    def get_release(self, release_id: str) -> Release | None:
        def query(connection: sqlite3.Connection) -> Release | None:
            row = connection.execute(
                f"""
                {_RELEASE_GROUP_SELECT}
                WHERE t.album_id = ?
                GROUP BY t.album_id, t.album, t.album_artist
                """,
                (release_id,),
            ).fetchone()
            if row is None:
                return None
            art_uri = self._query_art_uri_for_release(connection, release_id)
            return self._row_to_release(row, art_uri=art_uri)

        return self._with_connection(query)

    @_locked_db
    def get_release_tracks(self, release_id: str) -> list[Track]:
        def query(connection: sqlite3.Connection) -> list[Track]:
            rows = connection.execute(
                """
                SELECT
                    t.id,
                    t.title,
                    t.artist,
                    t.album,
                    t.album_artist,
                    t.disc_number,
                    t.track_number,
                    f.duration_sec,
                    aa.art_uri
                FROM tracks t
                JOIN files f ON f.id = t.file_id
                LEFT JOIN album_art aa ON aa.album_id = t.album_id
                WHERE t.album_id = ?
                ORDER BY t.disc_number NULLS LAST, t.track_number NULLS LAST, t.title COLLATE NOCASE
                """,
                (release_id,),
            ).fetchall()
            return [self._row_to_track(row) for row in rows]

        return self._with_connection(query)

    @_locked_db
    def get_track(self, track_id: str) -> Track | None:
        def query(connection: sqlite3.Connection) -> Track | None:
            row = connection.execute(
                """
                SELECT
                    t.id,
                    t.title,
                    t.artist,
                    t.album,
                    t.album_artist,
                    t.disc_number,
                    t.track_number,
                    f.duration_sec,
                    aa.art_uri
                FROM tracks t
                JOIN files f ON f.id = t.file_id
                LEFT JOIN album_art aa ON aa.album_id = t.album_id
                WHERE t.id = ?
                """,
                (track_id,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_track(row)

        return self._with_connection(query)

    @_locked_db
    def release_id_for_track(self, track_id: str) -> str | None:
        def query(connection: sqlite3.Connection) -> str | None:
            row = connection.execute(
                "SELECT album_id FROM tracks WHERE id = ?",
                (track_id,),
            ).fetchone()
            return None if row is None else str(row["album_id"])

        return self._with_connection(query)

    @_locked_db
    def get_file_metadata(self, track_id: str) -> FileMetadata | None:
        def query(connection: sqlite3.Connection) -> FileMetadata | None:
            row = connection.execute(
                """
                SELECT
                    f.path,
                    f.codec,
                    f.duration_sec,
                    f.sample_rate,
                    f.bit_depth,
                    f.channels
                FROM tracks t
                JOIN files f ON f.id = t.file_id
                WHERE t.id = ?
                """,
                (track_id,),
            ).fetchone()
            if row is None:
                return None
            return FileMetadata(
                path=row["path"],
                codec=row["codec"],
                duration_sec=row["duration_sec"],
                sample_rate=row["sample_rate"],
                bit_depth=row["bit_depth"],
                channels=row["channels"],
            )

        return self._with_connection(query)

    @_locked_db
    def search_releases(
        self,
        query: str,
        *,
        limit: int = 50,
        artists_only: bool = False,
    ) -> list[Release]:
        needle = f"%{query.strip()}%"

        def search(connection: sqlite3.Connection) -> list[Release]:
            release_ids: list[str] = []
            seen: set[str] = set()

            def add_ids(rows: list[sqlite3.Row]) -> None:
                for row in rows:
                    release_id = str(row["release_id"])
                    if release_id not in seen:
                        seen.add(release_id)
                        release_ids.append(release_id)

            if artists_only:
                add_ids(
                    connection.execute(
                        """
                        SELECT DISTINCT t.album_id AS release_id
                        FROM tracks t
                        WHERE t.album_artist LIKE ? COLLATE NOCASE
                        LIMIT ?
                        """,
                        (needle, limit),
                    ).fetchall(),
                )
            else:
                add_ids(
                    connection.execute(
                        f"""
                        SELECT DISTINCT t.album_id AS release_id
                        FROM tracks t
                        WHERE t.album LIKE ? COLLATE NOCASE
                           OR t.album_artist LIKE ? COLLATE NOCASE
                        LIMIT ?
                        """,
                        (needle, needle, limit),
                    ).fetchall(),
                )
                add_ids(
                    connection.execute(
                        """
                        SELECT DISTINCT t.album_id AS release_id
                        FROM tracks t
                        WHERE t.title LIKE ? COLLATE NOCASE
                           OR t.artist LIKE ? COLLATE NOCASE
                        LIMIT ?
                        """,
                        (needle, needle, limit),
                    ).fetchall(),
                )

            if not release_ids:
                return []

            placeholders = ",".join("?" * len(release_ids))
            rows = connection.execute(
                f"""
                {_RELEASE_GROUP_SELECT}
                WHERE t.album_id IN ({placeholders})
                GROUP BY t.album_id, t.album, t.album_artist
                ORDER BY t.album_artist COLLATE NOCASE, t.album COLLATE NOCASE
                """,
                release_ids,
            ).fetchall()
            by_id = {str(row["release_id"]): row for row in rows}
            ordered = [by_id[release_id] for release_id in release_ids if release_id in by_id]
            return self._rows_to_releases(ordered, connection=connection)

        return self._with_connection(search)

    @_locked_db
    def list_recently_added_items(
        self,
        *,
        within_days: int = 30,
        limit: int = 80,
    ) -> list[RecentlyAddedItem]:
        cutoff_ns = time.time_ns() - int(within_days * 86_400 * 1_000_000_000)

        def query(connection: sqlite3.Connection) -> list[RecentlyAddedItem]:
            rows = connection.execute(
                f"""
                {_RELEASE_GROUP_SELECT}
                GROUP BY t.album_id, t.album, t.album_artist
                HAVING MAX(f.indexed_at_ns) >= ?
                ORDER BY MAX(f.indexed_at_ns) DESC
                LIMIT ?
                """,
                (cutoff_ns, limit),
            ).fetchall()
            items: list[RecentlyAddedItem] = []
            for row in rows:
                release_id = str(row["release_id"])
                release = self._row_to_release(
                    row,
                    art_uri=self._query_art_uri_for_release(connection, release_id),
                )
                added_row = connection.execute(
                    """
                    SELECT MAX(f.indexed_at_ns) AS added_ns
                    FROM tracks t
                    JOIN files f ON f.id = t.file_id
                    WHERE t.album_id = ?
                    """,
                    (release_id,),
                ).fetchone()
                added_ns = int(added_row["added_ns"]) if added_row else 0
                items.append(RecentlyAddedItem(added_ns=added_ns, release=release))
            return items

        return self._with_connection(query)

    @_locked_db
    def record_play(
        self,
        *,
        track_id: str,
        release_id: str,
        source: str,
        played_at_ns: int | None = None,
    ) -> None:
        if self._write_connection is None:
            raise RuntimeError("library store write connection is closed")
        when = played_at_ns if played_at_ns is not None else time.time_ns()

        def attempt() -> None:
            try:
                self._write_connection.execute(
                    """
                    INSERT INTO play_history(track_id, release_id, source, played_at_ns)
                    VALUES (?, ?, ?, ?)
                    """,
                    (track_id, release_id, source, when),
                )
                self._write_connection.commit()
            except Exception:
                try:
                    self._write_connection.rollback()
                except sqlite3.OperationalError:
                    pass
                raise

        def rollback_on_locked(_exc: sqlite3.OperationalError, _attempt: int) -> None:
            try:
                self._write_connection.rollback()
            except sqlite3.OperationalError:
                pass

        with_db_retry(attempt, on_locked=rollback_on_locked)

    @_locked_db
    def last_play_at_ns(self, track_id: str) -> int | None:
        def query(connection: sqlite3.Connection) -> int | None:
            row = connection.execute(
                """
                SELECT played_at_ns FROM play_history
                WHERE track_id = ?
                ORDER BY played_at_ns DESC
                LIMIT 1
                """,
                (track_id,),
            ).fetchone()
            return None if row is None else int(row["played_at_ns"])

        return self._with_connection(query)

    @_locked_db
    def list_continue_listening_entries(
        self,
        *,
        limit: int = SUGGESTIONS_LOCAL_CONTINUE_LIMIT,
    ) -> list[tuple[str, int]]:
        """Recently played release ids (any source), newest first."""

        def query(connection: sqlite3.Connection) -> list[tuple[str, int]]:
            rows = connection.execute(
                """
                SELECT release_id, MAX(played_at_ns) AS last_played_ns
                FROM play_history
                GROUP BY release_id
                ORDER BY last_played_ns DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [(str(row["release_id"]), int(row["last_played_ns"])) for row in rows]

        return self._with_connection(query)

    @_locked_db
    def list_rediscover_items(
        self,
        *,
        limit: int = SUGGESTIONS_LOCAL_REDISCOVER_LIMIT,
        idle_months: int = SUGGESTIONS_REDISCOVER_IDLE_MONTHS,
        recent_genre_days: int = SUGGESTIONS_RECENT_GENRE_DAYS,
    ) -> list[RecentlyAddedItem]:
        genre_cutoff_ns = time.time_ns() - int(recent_genre_days * 86_400 * 1_000_000_000)
        idle_cutoff_ns = time.time_ns() - int(idle_months * 30 * 86_400 * 1_000_000_000)

        def query(connection: sqlite3.Connection) -> list[RecentlyAddedItem]:
            genre_rows = connection.execute(
                """
                SELECT DISTINCT t.genre AS genre
                FROM play_history ph
                JOIN tracks t ON t.id = ph.track_id
                WHERE ph.source = ?
                  AND ph.played_at_ns >= ?
                  AND t.genre IS NOT NULL
                  AND trim(t.genre) != ''
                """,
                (Source.LOCAL.value, genre_cutoff_ns),
            ).fetchall()
            genres = [str(row["genre"]) for row in genre_rows if row["genre"]]
            if not genres:
                return []
            placeholders = ",".join("?" * len(genres))
            rows = connection.execute(
                f"""
                {_RELEASE_GROUP_SELECT}
                WHERE t.genre IN ({placeholders})
                  AND t.album_id NOT IN (
                      SELECT release_id
                      FROM play_history
                      WHERE played_at_ns >= ?
                  )
                GROUP BY t.album_id, t.album, t.album_artist
                ORDER BY RANDOM()
                LIMIT ?
                """,
                (*genres, idle_cutoff_ns, limit),
            ).fetchall()
            items: list[RecentlyAddedItem] = []
            for row in rows:
                release_id = str(row["release_id"])
                release = self._row_to_release(
                    row,
                    art_uri=self._query_art_uri_for_release(connection, release_id),
                )
                last_row = connection.execute(
                    """
                    SELECT MAX(played_at_ns) AS last_played_ns
                    FROM play_history
                    WHERE release_id = ?
                    """,
                    (release_id,),
                ).fetchone()
                added_ns = (
                    int(last_row["last_played_ns"])
                    if last_row and last_row["last_played_ns"]
                    else 0
                )
                items.append(RecentlyAddedItem(added_ns=added_ns, release=release))
            return items

        return self._with_connection(query)

    @staticmethod
    def _normalize_label_name(name: str) -> str:
        return name.strip()

    @staticmethod
    def _prune_stale_release_labels(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM release_labels
            WHERE (
                release_id GLOB 'local:*'
                AND CASE
                    WHEN instr(release_id, '@') > 0
                    THEN substr(release_id, 1, instr(release_id, '@') - 1)
                    ELSE release_id
                END NOT IN (SELECT DISTINCT album_id FROM tracks)
            )
            OR (release_id GLOB 'tidal:*' AND release_id NOT GLOB 'tidal:album:*')
            OR (release_id GLOB 'qobuz:*' AND release_id NOT GLOB 'qobuz:album:*')
            """,
        )

    @staticmethod
    def _prune_orphan_user_labels(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM user_labels
            WHERE NOT EXISTS (
                SELECT 1
                FROM release_labels rl
                WHERE rl.label_id = user_labels.id
            )
            """,
        )

    @staticmethod
    def _prune_release_label_tables(connection: sqlite3.Connection) -> None:
        LibraryStore._prune_stale_release_labels(connection)
        LibraryStore._prune_orphan_user_labels(connection)

    @_locked_db
    def prune_release_label_tables(self) -> None:
        if self._write_connection is None:
            raise RuntimeError("library store write connection is closed")

        def attempt() -> None:
            try:
                self._prune_release_label_tables(self._write_connection)
                self._write_connection.commit()
            except Exception:
                try:
                    self._write_connection.rollback()
                except sqlite3.OperationalError:
                    pass
                raise

        def rollback_on_locked(_exc: sqlite3.OperationalError, _attempt: int) -> None:
            try:
                self._write_connection.rollback()
            except sqlite3.OperationalError:
                pass

        with_db_retry(attempt, on_locked=rollback_on_locked)

    @_locked_db
    def list_user_label_names(self) -> tuple[str, ...]:
        def query(connection: sqlite3.Connection) -> tuple[str, ...]:
            rows = connection.execute(
                """
                SELECT DISTINCT ul.name
                FROM user_labels ul
                JOIN release_labels rl ON rl.label_id = ul.id
                ORDER BY ul.name COLLATE NOCASE
                """,
            ).fetchall()
            return tuple(str(row["name"]) for row in rows)

        return self._with_connection(query)

    @_locked_db
    def get_release_label_names(self, release_id: str) -> frozenset[str]:
        def query(connection: sqlite3.Connection) -> frozenset[str]:
            rows = connection.execute(
                """
                SELECT ul.name
                FROM release_labels rl
                JOIN user_labels ul ON ul.id = rl.label_id
                WHERE rl.release_id = ?
                ORDER BY ul.name COLLATE NOCASE
                """,
                (release_id,),
            ).fetchall()
            return frozenset(str(row["name"]) for row in rows)

        return self._with_connection(query)

    @_locked_db
    def labels_for_release_ids(
        self,
        release_ids: list[str],
    ) -> dict[str, frozenset[str]]:
        if not release_ids:
            return {}

        def query(connection: sqlite3.Connection) -> dict[str, frozenset[str]]:
            placeholders = ",".join("?" * len(release_ids))
            rows = connection.execute(
                f"""
                SELECT rl.release_id, ul.name
                FROM release_labels rl
                JOIN user_labels ul ON ul.id = rl.label_id
                WHERE rl.release_id IN ({placeholders})
                ORDER BY ul.name COLLATE NOCASE
                """,
                release_ids,
            ).fetchall()
            result: dict[str, set[str]] = {release_id: set() for release_id in release_ids}
            for row in rows:
                result.setdefault(str(row["release_id"]), set()).add(str(row["name"]))
            return {
                release_id: frozenset(names)
                for release_id, names in result.items()
            }

        return self._with_connection(query)

    @_locked_db
    def list_flagged_release_ids(self) -> tuple[str, ...]:
        def query(connection: sqlite3.Connection) -> tuple[str, ...]:
            rows = connection.execute(
                """
                SELECT release_id, MAX(tagged_at_ns) AS latest
                FROM release_labels
                GROUP BY release_id
                ORDER BY latest DESC
                """,
            ).fetchall()
            return tuple(str(row["release_id"]) for row in rows)

        return self._with_connection(query)

    def _label_id_for_name(
        self,
        connection: sqlite3.Connection,
        name: str,
        *,
        create: bool = False,
    ) -> int | None:
        normalized = self._normalize_label_name(name)
        if not normalized:
            return None
        row = connection.execute(
            "SELECT id, name FROM user_labels WHERE name = ? COLLATE NOCASE",
            (normalized,),
        ).fetchone()
        if row is not None:
            return int(row["id"])
        if not create:
            return None
        when = time.time_ns()
        cursor = connection.execute(
            "INSERT INTO user_labels(name, created_at_ns) VALUES (?, ?)",
            (normalized, when),
        )
        return int(cursor.lastrowid)

    @_locked_db
    def ensure_user_label(self, name: str) -> str:
        if self._write_connection is None:
            raise RuntimeError("library store write connection is closed")
        normalized = self._normalize_label_name(name)
        if not normalized:
            raise ValueError("label name must not be empty")

        def attempt() -> str:
            try:
                label_id = self._label_id_for_name(
                    self._write_connection,
                    normalized,
                    create=True,
                )
                if label_id is None:
                    raise ValueError("label name must not be empty")
                row = self._write_connection.execute(
                    "SELECT name FROM user_labels WHERE id = ?",
                    (label_id,),
                ).fetchone()
                self._write_connection.commit()
                return str(row["name"]) if row is not None else normalized
            except Exception:
                try:
                    self._write_connection.rollback()
                except sqlite3.OperationalError:
                    pass
                raise

        def rollback_on_locked(_exc: sqlite3.OperationalError, _attempt: int) -> None:
            try:
                self._write_connection.rollback()
            except sqlite3.OperationalError:
                pass

        return with_db_retry(attempt, on_locked=rollback_on_locked)

    @_locked_db
    def set_release_labels(self, release_id: str, labels: frozenset[str]) -> None:
        if self._write_connection is None:
            raise RuntimeError("library store write connection is closed")
        normalized = frozenset(
            self._normalize_label_name(label)
            for label in labels
            if self._normalize_label_name(label)
        )

        def attempt() -> None:
            try:
                self._write_connection.execute(
                    "DELETE FROM release_labels WHERE release_id = ?",
                    (release_id,),
                )
                when = time.time_ns()
                for label in sorted(normalized, key=lambda item: item.casefold()):
                    label_id = self._label_id_for_name(
                        self._write_connection,
                        label,
                        create=True,
                    )
                    if label_id is None:
                        continue
                    self._write_connection.execute(
                        """
                        INSERT INTO release_labels(release_id, label_id, tagged_at_ns)
                        VALUES (?, ?, ?)
                        """,
                        (release_id, label_id, when),
                    )
                self._prune_orphan_user_labels(self._write_connection)
                self._write_connection.commit()
            except Exception:
                try:
                    self._write_connection.rollback()
                except sqlite3.OperationalError:
                    pass
                raise

        def rollback_on_locked(_exc: sqlite3.OperationalError, _attempt: int) -> None:
            try:
                self._write_connection.rollback()
            except sqlite3.OperationalError:
                pass

        with_db_retry(attempt, on_locked=rollback_on_locked)

    @_locked_db
    def toggle_release_label(self, release_id: str, label: str, *, on: bool) -> None:
        if self._write_connection is None:
            raise RuntimeError("library store write connection is closed")
        normalized = self._normalize_label_name(label)
        if not normalized:
            raise ValueError("label name must not be empty")

        def attempt() -> None:
            try:
                if on:
                    label_id = self._label_id_for_name(
                        self._write_connection,
                        normalized,
                        create=True,
                    )
                    if label_id is None:
                        raise ValueError("label name must not be empty")
                    when = time.time_ns()
                    self._write_connection.execute(
                        """
                        INSERT OR IGNORE INTO release_labels(release_id, label_id, tagged_at_ns)
                        VALUES (?, ?, ?)
                        """,
                        (release_id, label_id, when),
                    )
                else:
                    self._write_connection.execute(
                        """
                        DELETE FROM release_labels
                        WHERE release_id = ?
                          AND label_id IN (
                              SELECT id FROM user_labels WHERE name = ? COLLATE NOCASE
                          )
                        """,
                        (release_id, normalized),
                    )
                self._prune_orphan_user_labels(self._write_connection)
                self._write_connection.commit()
            except Exception:
                try:
                    self._write_connection.rollback()
                except sqlite3.OperationalError:
                    pass
                raise

        def rollback_on_locked(_exc: sqlite3.OperationalError, _attempt: int) -> None:
            try:
                self._write_connection.rollback()
            except sqlite3.OperationalError:
                pass

        with_db_retry(attempt, on_locked=rollback_on_locked)

    @staticmethod
    def quality_hint(metadata: FileMetadata | None) -> str:
        from tunes_player.core.playback_quality import local_file_format_label

        return local_file_format_label(metadata)

    def _rows_to_releases(
        self,
        rows: list[sqlite3.Row],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[Release]:
        if not rows:
            return []
        release_ids = [str(row["release_id"]) for row in rows]
        if connection is not None:
            art_by_release = self._query_art_uri_map(connection, release_ids)
        else:
            art_by_release = self.art_uri_map(release_ids)
        return [
            self._row_to_release(row, art_uri=art_by_release.get(str(row["release_id"])))
            for row in rows
        ]

    @staticmethod
    def _query_art_uri_map(
        connection: sqlite3.Connection,
        release_ids: list[str],
    ) -> dict[str, str | None]:
        if not release_ids:
            return {}
        placeholders = ",".join("?" * len(release_ids))
        rows = connection.execute(
            f"SELECT album_id, art_uri FROM album_art WHERE album_id IN ({placeholders})",
            release_ids,
        ).fetchall()
        found = {str(row["album_id"]): str(row["art_uri"]) for row in rows}
        return {release_id: found.get(release_id) for release_id in release_ids}

    @staticmethod
    def _query_art_uri_for_release(
        connection: sqlite3.Connection,
        release_id: str,
    ) -> str | None:
        row = connection.execute(
            "SELECT art_uri FROM album_art WHERE album_id = ?",
            (release_id,),
        ).fetchone()
        return None if row is None else str(row["art_uri"])

    @_locked_db
    def _art_uri_for_release(self, release_id: str) -> str | None:
        def query(connection: sqlite3.Connection) -> str | None:
            return self._query_art_uri_for_release(connection, release_id)

        return self._with_connection(query)

    @_locked_db
    def art_uri_map(self, release_ids: list[str]) -> dict[str, str | None]:
        """Return art_uri per release id (missing entries are None)."""
        if not release_ids:
            return {}

        def query(connection: sqlite3.Connection) -> dict[str, str | None]:
            return self._query_art_uri_map(connection, release_ids)

        return self._with_connection(query)

    def _art_uri_map(self, release_ids: list[str]) -> dict[str, str]:
        return {
            release_id: art_uri
            for release_id, art_uri in self.art_uri_map(release_ids).items()
            if art_uri is not None
        }

    def _row_to_release(self, row: sqlite3.Row, *, art_uri: str | None = None) -> Release:
        track_count = int(row["track_count"])
        is_synthetic = bool(int(row["is_synthetic"] or 0))
        total_tracks_tag = row["total_tracks_tag"]
        max_track_number = row["max_track_number"]
        tag_raw = row["release_type_tag"]
        release_type_tag = str(tag_raw) if tag_raw else None
        completeness, release_type, expected = infer_release_metadata(
            track_count=track_count,
            is_synthetic=is_synthetic,
            total_tracks_tag=int(total_tracks_tag) if total_tracks_tag is not None else None,
            max_track_number=int(max_track_number) if max_track_number is not None else None,
            release_type_tag=release_type_tag,
        )
        duration = row["duration_sec"]
        from tunes_player.core.release_quality import (
            max_quality_tier,
            tiers_from_local,
        )

        max_bit_depth = row["max_bit_depth"]
        max_sample_rate = row["max_sample_rate"]
        has_lossless = bool(int(row["has_lossless"] or 0))
        has_lossy = bool(int(row["has_lossy"] or 0))
        available_quality_tiers = tiers_from_local(
            max_bit_depth=int(max_bit_depth) if max_bit_depth is not None else None,
            max_sample_rate=int(max_sample_rate) if max_sample_rate is not None else None,
            has_lossless=has_lossless,
            has_lossy=has_lossy,
        )
        peak_quality_tier = max_quality_tier(*available_quality_tiers)
        release_id = str(row["release_id"])
        return Release(
            id=release_id,
            title=row["album"] or "Unknown",
            artist_name=row["album_artist"] or "Unknown Artist",
            source=Source.LOCAL,
            track_count=track_count,
            expected_track_count=expected,
            completeness=completeness,
            release_type=release_type,
            year=row["year"],
            genre=row["genre"],
            art_uri=art_uri,
            duration_sec=float(duration) if duration is not None else None,
            peak_quality_tier=peak_quality_tier,
            available_quality_tiers=available_quality_tiers,
            catalog_release_id=release_id,
            peak_sample_rate_hz=(
                int(max_sample_rate) if max_sample_rate is not None else None
            ),
            peak_bit_depth=int(max_bit_depth) if max_bit_depth is not None else None,
        )

    @staticmethod
    def _row_to_track(row: sqlite3.Row) -> Track:
        duration = row["duration_sec"] if "duration_sec" in row.keys() else None
        art_uri = row["art_uri"] if "art_uri" in row.keys() and row["art_uri"] else None
        return Track(
            id=row["id"],
            title=row["title"] or "Unknown Title",
            artist_name=row["artist"] or row["album_artist"] or "Unknown Artist",
            release_title=row["album"],
            source=Source.LOCAL,
            duration_sec=duration,
            art_uri=art_uri,
            track_number=row["track_number"] if "track_number" in row.keys() else None,
            disc_number=row["disc_number"] if "disc_number" in row.keys() else None,
        )
