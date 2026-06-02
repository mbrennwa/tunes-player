"""Read models from the local library database."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from tunes_player.core.home import RecentlyAddedItem
from tunes_player.core.library import ids
from tunes_player.core.library.db import connect
from tunes_player.core.models import Album, Artist, Source, Track


@dataclass(frozen=True, slots=True)
class FileMetadata:
    path: str
    codec: str | None
    duration_sec: float | None
    sample_rate: int | None
    bit_depth: int | None
    channels: int | None


class LibraryStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._connection = connect(db_path)

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def close(self) -> None:
        self._connection.close()

    def reconnect(self) -> None:
        """Reopen the DB connection (call after a scan from another connection)."""
        self._connection.close()
        self._connection = connect(self._db_path)

    def track_count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) AS count FROM tracks").fetchone()
        return int(row["count"])

    def list_albums(self) -> list[Album]:
        rows = self._connection.execute(
            """
            SELECT
                album_id,
                album,
                album_artist,
                MIN(year) AS year,
                COUNT(*) AS track_count
            FROM tracks
            GROUP BY album_id, album, album_artist
            ORDER BY album_artist COLLATE NOCASE, album COLLATE NOCASE
            """,
        ).fetchall()
        art_by_album = self._art_uri_map([row["album_id"] for row in rows])
        return [
            self._row_to_album(row, art_uri=art_by_album.get(row["album_id"]))
            for row in rows
        ]

    def list_artists(self) -> list[Artist]:
        rows = self._connection.execute(
            """
            SELECT DISTINCT album_artist AS name
            FROM tracks
            WHERE album_artist != ''
            ORDER BY name COLLATE NOCASE
            """,
        ).fetchall()
        return [
            Artist(id=ids.artist_id(row["name"]), name=row["name"], source=Source.LOCAL)
            for row in rows
        ]

    def get_album(self, album_id: str) -> Album | None:
        row = self._connection.execute(
            """
            SELECT album_id, album, album_artist, MIN(year) AS year, COUNT(*) AS track_count
            FROM tracks
            WHERE album_id = ?
            GROUP BY album_id, album, album_artist
            """,
            (album_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_album(row, art_uri=self._art_uri_for_album(album_id))

    def get_album_tracks(self, album_id: str) -> list[Track]:
        rows = self._connection.execute(
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
            (album_id,),
        ).fetchall()
        return [self._row_to_track(row) for row in rows]

    def get_artist(self, artist_id: str) -> Artist | None:
        for artist in self.list_artists():
            if artist.id == artist_id:
                return artist
        return None

    def get_artist_albums(self, artist_id: str) -> list[Album]:
        artist = self.get_artist(artist_id)
        if artist is None:
            return []
        rows = self._connection.execute(
            """
            SELECT
                album_id,
                album,
                album_artist,
                MIN(year) AS year,
                COUNT(*) AS track_count
            FROM tracks
            WHERE album_artist = ?
            GROUP BY album_id, album, album_artist
            ORDER BY album COLLATE NOCASE
            """,
            (artist.name,),
        ).fetchall()
        art_by_album = self._art_uri_map([row["album_id"] for row in rows])
        return [
            self._row_to_album(row, art_uri=art_by_album.get(row["album_id"]))
            for row in rows
        ]

    def get_track(self, track_id: str) -> Track | None:
        row = self._connection.execute(
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

    def album_id_for_track(self, track_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT album_id FROM tracks WHERE id = ?",
            (track_id,),
        ).fetchone()
        return None if row is None else str(row["album_id"])

    def get_file_metadata(self, track_id: str) -> FileMetadata | None:
        row = self._connection.execute(
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

    def search(self, query: str) -> tuple[list[Album], list[Track]]:
        needle = f"%{query.strip()}%"
        album_rows = self._connection.execute(
            """
            SELECT
                album_id,
                album,
                album_artist,
                MIN(year) AS year,
                COUNT(*) AS track_count
            FROM tracks
            WHERE album LIKE ? COLLATE NOCASE
               OR album_artist LIKE ? COLLATE NOCASE
            GROUP BY album_id, album, album_artist
            ORDER BY album_artist COLLATE NOCASE, album COLLATE NOCASE
            LIMIT 50
            """,
            (needle, needle),
        ).fetchall()
        track_rows = self._connection.execute(
            """
            SELECT t.id, t.title, t.artist, t.album, t.album_artist, f.duration_sec, aa.art_uri
            FROM tracks t
            JOIN files f ON f.id = t.file_id
            LEFT JOIN album_art aa ON aa.album_id = t.album_id
            WHERE t.title LIKE ? COLLATE NOCASE
               OR t.artist LIKE ? COLLATE NOCASE
               OR t.album LIKE ? COLLATE NOCASE
            ORDER BY t.album_artist COLLATE NOCASE, t.album COLLATE NOCASE, t.title COLLATE NOCASE
            LIMIT 100
            """,
            (needle, needle, needle),
        ).fetchall()
        art_by_album = self._art_uri_map([row["album_id"] for row in album_rows])
        albums = [
            self._row_to_album(row, art_uri=art_by_album.get(row["album_id"]))
            for row in album_rows
        ]
        tracks = [self._row_to_track(row) for row in track_rows]
        return albums, tracks

    def list_recently_added_items(
        self,
        *,
        within_days: int = 30,
        album_limit: int = 40,
        track_limit: int = 80,
    ) -> list[RecentlyAddedItem]:
        cutoff_ns = time.time_ns() - int(within_days * 86_400 * 1_000_000_000)
        album_rows = self._connection.execute(
            """
            SELECT
                t.album_id,
                t.album,
                t.album_artist,
                MIN(t.year) AS year,
                COUNT(*) AS track_count,
                MAX(f.indexed_at_ns) AS added_ns
            FROM tracks t
            JOIN files f ON f.id = t.file_id
            GROUP BY t.album_id, t.album, t.album_artist
            HAVING added_ns >= ?
            ORDER BY added_ns DESC
            LIMIT ?
            """,
            (cutoff_ns, album_limit),
        ).fetchall()
        track_rows = self._connection.execute(
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
                aa.art_uri,
                f.indexed_at_ns AS added_ns
            FROM tracks t
            JOIN files f ON f.id = t.file_id
            LEFT JOIN album_art aa ON aa.album_id = t.album_id
            WHERE f.indexed_at_ns >= ?
            ORDER BY f.indexed_at_ns DESC
            LIMIT ?
            """,
            (cutoff_ns, track_limit),
        ).fetchall()
        art_by_album = self._art_uri_map([row["album_id"] for row in album_rows])
        items: list[RecentlyAddedItem] = []
        for row in album_rows:
            album = self._row_to_album(row, art_uri=art_by_album.get(row["album_id"]))
            items.append(
                RecentlyAddedItem(
                    kind="album",
                    added_ns=int(row["added_ns"]),
                    album=album,
                )
            )
        for row in track_rows:
            track = self._row_to_track(row)
            items.append(
                RecentlyAddedItem(
                    kind="track",
                    added_ns=int(row["added_ns"]),
                    track=track,
                )
            )
        items.sort(key=lambda item: item.added_ns, reverse=True)
        return items

    @staticmethod
    def quality_hint(metadata: FileMetadata | None) -> str:
        if metadata is None:
            return "Local file"
        codec = (metadata.codec or "audio").upper()
        if metadata.sample_rate:
            rate_khz = metadata.sample_rate / 1000
            rate_text = f"{rate_khz:g}"
            if metadata.bit_depth:
                return f"{codec} · {metadata.bit_depth}/{rate_text} kHz · {metadata.channels or 2}ch"
            return f"{codec} · {rate_text} kHz · {metadata.channels or 2}ch"
        if codec == "MP3":
            return f"MP3 · {metadata.channels or 2}ch"
        return f"{codec} · {metadata.channels or 2}ch"

    def _art_uri_for_album(self, album_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT art_uri FROM album_art WHERE album_id = ?",
            (album_id,),
        ).fetchone()
        return None if row is None else str(row["art_uri"])

    def _art_uri_map(self, album_ids: list[str]) -> dict[str, str]:
        if not album_ids:
            return {}
        placeholders = ",".join("?" * len(album_ids))
        rows = self._connection.execute(
            f"SELECT album_id, art_uri FROM album_art WHERE album_id IN ({placeholders})",
            album_ids,
        ).fetchall()
        return {str(row["album_id"]): str(row["art_uri"]) for row in rows}

    @staticmethod
    def _row_to_album(row: sqlite3.Row, *, art_uri: str | None = None) -> Album:
        return Album(
            id=row["album_id"],
            title=row["album"] or "Unknown Album",
            artist_name=row["album_artist"] or "Unknown Artist",
            source=Source.LOCAL,
            year=row["year"],
            track_count=int(row["track_count"]),
            art_uri=art_uri,
        )

    @staticmethod
    def _row_to_track(row: sqlite3.Row) -> Track:
        duration = row["duration_sec"] if "duration_sec" in row.keys() else None
        art_uri = row["art_uri"] if "art_uri" in row.keys() and row["art_uri"] else None
        return Track(
            id=row["id"],
            title=row["title"] or "Unknown Title",
            artist_name=row["artist"] or row["album_artist"] or "Unknown Artist",
            album_title=row["album"],
            source=Source.LOCAL,
            duration_sec=duration,
            art_uri=art_uri,
            track_number=row["track_number"] if "track_number" in row.keys() else None,
            disc_number=row["disc_number"] if "disc_number" in row.keys() else None,
        )
