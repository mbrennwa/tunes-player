"""Extract embedded cover art and write it to the on-disk cache."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tunes_player.core.art import art_cache_path, find_cached_art_path, local_art_uri


def extract_embedded_art(path: Path) -> tuple[bytes, str] | None:
    suffix = path.suffix.casefold()
    try:
        if suffix == ".flac":
            return _extract_flac_art(path)
        if suffix == ".mp3":
            return _extract_mp3_art(path)
        if suffix == ".ogg":
            return _extract_ogg_art(path)
        if suffix in {".m4a", ".aac"}:
            return _extract_mp4_art(path)
        if suffix in {".wav", ".aiff", ".aif"}:
            return _extract_generic_art(path)
    except OSError:
        return None
    return None


def upsert_album_art(
    connection: sqlite3.Connection,
    *,
    data_dir: Path,
    album_id: str,
    art_data: bytes,
    mime_type: str,
) -> None:
    target = art_cache_path(data_dir, album_id, mime_type)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = find_cached_art_path(data_dir, album_id)
    if existing is not None and existing != target:
        existing.unlink(missing_ok=True)
    target.write_bytes(art_data)
    connection.execute(
        """
        INSERT INTO album_art(album_id, art_uri, mime_type, updated_at)
        VALUES (?, ?, ?, strftime('%s', 'now'))
        ON CONFLICT(album_id) DO UPDATE SET
            art_uri = excluded.art_uri,
            mime_type = excluded.mime_type,
            updated_at = excluded.updated_at
        """,
        (album_id, local_art_uri(album_id), mime_type),
    )


def index_album_art_for_file(
    connection: sqlite3.Connection,
    *,
    data_dir: Path,
    path: Path,
    album_id: str,
) -> bool:
    """Extract embedded art from *path* into the cache for *album_id*."""
    art = extract_embedded_art(path)
    if art is None:
        return False
    data, mime_type = art
    upsert_album_art(
        connection,
        data_dir=data_dir,
        album_id=album_id,
        art_data=data,
        mime_type=mime_type,
    )
    return True


def repair_stale_album_art(connection: sqlite3.Connection, *, data_dir: Path) -> int:
    """Re-extract art when album_art exists but the on-disk cache file is missing."""
    rows = connection.execute(
        """
        SELECT aa.album_id, MIN(f.path) AS path
        FROM album_art AS aa
        JOIN tracks AS t ON t.album_id = aa.album_id
        JOIN files AS f ON f.id = t.file_id
        GROUP BY aa.album_id
        """,
    ).fetchall()
    repaired = 0
    for row in rows:
        album_id = str(row["album_id"])
        if find_cached_art_path(data_dir, album_id) is not None:
            continue
        if index_album_art_for_file(
            connection,
            data_dir=data_dir,
            path=Path(str(row["path"])),
            album_id=album_id,
        ):
            repaired += 1
            continue
        connection.execute("DELETE FROM album_art WHERE album_id = ?", (album_id,))
    return repaired


def backfill_missing_album_art(connection: sqlite3.Connection, *, data_dir: Path) -> int:
    """Extract embedded art for albums that have tracks but no cached cover yet."""
    rows = connection.execute(
        """
        SELECT t.album_id, t.album_artist, t.album, MIN(f.path) AS path
        FROM tracks AS t
        JOIN files AS f ON f.id = t.file_id
        LEFT JOIN album_art AS aa ON aa.album_id = t.album_id
        WHERE aa.album_id IS NULL
        GROUP BY t.album_id, t.album_artist, t.album
        """,
    ).fetchall()
    indexed = 0
    for row in rows:
        before = connection.execute(
            "SELECT 1 FROM album_art WHERE album_id = ?",
            (row["album_id"],),
        ).fetchone()
        index_album_art_for_file(
            connection,
            data_dir=data_dir,
            path=Path(str(row["path"])),
            album_id=str(row["album_id"]),
        )
        after = connection.execute(
            "SELECT 1 FROM album_art WHERE album_id = ?",
            (row["album_id"],),
        ).fetchone()
        if before is None and after is not None:
            indexed += 1
    return indexed


def maintain_album_art(connection: sqlite3.Connection, *, data_dir: Path) -> tuple[int, int]:
    """Repair stale cache entries, backfill missing art, and prune orphans."""
    repaired = repair_stale_album_art(connection, data_dir=data_dir)
    added = backfill_missing_album_art(connection, data_dir=data_dir)
    prune_orphan_album_art(connection, data_dir=data_dir)
    return added, repaired


def prune_orphan_album_art(connection: sqlite3.Connection, *, data_dir: Path) -> None:
    rows = connection.execute(
        """
        SELECT aa.album_id
        FROM album_art AS aa
        LEFT JOIN tracks AS t ON t.album_id = aa.album_id
        WHERE t.album_id IS NULL
        """,
    ).fetchall()
    for row in rows:
        album_id = str(row["album_id"])
        cached = find_cached_art_path(data_dir, album_id)
        if cached is not None:
            cached.unlink(missing_ok=True)
        connection.execute("DELETE FROM album_art WHERE album_id = ?", (album_id,))


def _extract_flac_art(path: Path) -> tuple[bytes, str] | None:
    from mutagen.flac import FLAC

    audio = FLAC(path)
    if not audio.pictures:
        return None
    picture = audio.pictures[0]
    return bytes(picture.data), picture.mime or "image/jpeg"


def _extract_mp3_art(path: Path) -> tuple[bytes, str] | None:
    from mutagen.id3 import ID3

    tags = ID3(path)
    for key in tags.keys():
        if not key.startswith("APIC"):
            continue
        frame = tags[key]
        return bytes(frame.data), frame.mime or "image/jpeg"
    return None


def _extract_ogg_art(path: Path) -> tuple[bytes, str] | None:
    from mutagen.oggvorbis import OggVorbis

    audio = OggVorbis(path)
    if not getattr(audio, "pictures", None):
        return None
    picture = audio.pictures[0]
    return bytes(picture.data), picture.mime or "image/jpeg"


def _extract_mp4_art(path: Path) -> tuple[bytes, str] | None:
    from mutagen.mp4 import MP4

    audio = MP4(path)
    covers = audio.tags.get("covr") if audio.tags else None
    if not covers:
        return None
    cover = covers[0]
    from mutagen.mp4 import MP4Cover

    mime = "image/png" if cover.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
    return bytes(cover), mime


def _extract_generic_art(path: Path) -> tuple[bytes, str] | None:
    from mutagen import File

    audio = File(path)
    if audio is None:
        return None
    pictures = getattr(audio, "pictures", None)
    if pictures:
        picture = pictures[0]
        return bytes(picture.data), picture.mime or "image/jpeg"
    return None
