"""Walk music folders and index Tier 1 audio files."""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tunes_player.core.config import AppConfig
from tunes_player.core.library import ids
from tunes_player.core.library.art_cache import (
    backfill_missing_album_art,
    index_album_art_for_file,
    prune_orphan_album_art,
)
from tunes_player.core.library.db import connect
from tunes_player.core.library.formats import codec_for_path, has_tier1_extension, is_tier1_path

ProgressCallback = Callable[[int, int, str], None]


@dataclass(frozen=True, slots=True)
class ScanResult:
    indexed: int
    removed: int
    skipped: int
    errors: int
    art_indexed: int = 0


@dataclass(frozen=True, slots=True)
class _ParsedTrack:
    path: str
    mtime_ns: int
    size_bytes: int
    codec: str | None
    duration_sec: float | None
    sample_rate: int | None
    bit_depth: int | None
    channels: int | None
    title: str
    artist: str
    album_artist: str
    album: str
    release_id: str
    is_synthetic: bool
    disc_number: int | None
    track_number: int | None
    year: int | None
    genre: str | None
    total_tracks: int | None
    release_type_tag: str | None


class LibraryScanner:
    _BATCH_SIZE = 50
    _PROGRESS_INTERVAL_SEC = 0.5
    _YIELD_EVERY = 8

    def __init__(self, *, db_path: Path, config: AppConfig) -> None:
        self._db_path = db_path
        self._data_dir = db_path.parent
        self._config = config

    def scan(
        self,
        *,
        scan_folders: list[str] | None = None,
        progress: ProgressCallback | None = None,
    ) -> ScanResult:
        if progress is not None:
            progress(0, 0, "Discovering files…")

        if scan_folders is None:
            roots = [
                str(Path(folder).resolve())
                for folder in self._config.music_folders
            ]
        else:
            roots = [str(Path(folder).resolve()) for folder in scan_folders]

        candidates = self._collect_candidates(roots=roots, progress=progress)
        seen_paths: set[str] = set()
        indexed = 0
        skipped = 0
        errors = 0
        total = len(candidates)
        last_progress_at = 0.0

        connection = connect(self._db_path)
        try:
            connection.execute("BEGIN")
            for index, path in enumerate(candidates, start=1):
                if index % self._YIELD_EVERY == 0:
                    time.sleep(0)

                if progress is not None:
                    now = time.monotonic()
                    if (
                        index == 1
                        or index == total
                        or now - last_progress_at >= self._PROGRESS_INTERVAL_SEC
                    ):
                        progress(index, total, str(path))
                        last_progress_at = now

                if not is_tier1_path(path):
                    skipped += 1
                    continue

                try:
                    stat = path.stat()
                except OSError:
                    errors += 1
                    continue

                path_str = str(path.resolve())
                seen_paths.add(path_str)

                existing = connection.execute(
                    "SELECT id, mtime_ns, size_bytes, indexed_at_ns FROM files WHERE path = ?",
                    (path_str,),
                ).fetchone()
                if (
                    existing is not None
                    and int(existing["mtime_ns"]) == stat.st_mtime_ns
                    and int(existing["size_bytes"]) == stat.st_size
                ):
                    if self._should_bump_indexed_at(
                        path_str=path_str,
                        indexed_at_ns=int(existing["indexed_at_ns"]),
                        mtime_ns=int(existing["mtime_ns"]),
                        file_mtime_ns=stat.st_mtime_ns,
                    ):
                        connection.execute(
                            "UPDATE files SET indexed_at_ns = ? WHERE id = ?",
                            (time.time_ns(), int(existing["id"])),
                        )
                    skipped += 1
                    continue

                try:
                    parsed = _parse_file(path, stat.st_mtime_ns, stat.st_size)
                except Exception:
                    errors += 1
                    continue

                if existing is not None:
                    connection.execute("DELETE FROM files WHERE id = ?", (existing["id"],))

                file_id = self._insert_file(connection, parsed, indexed_at_ns=time.time_ns())
                self._insert_track(connection, parsed, file_id)
                index_album_art_for_file(
                    connection,
                    data_dir=self._data_dir,
                    path=Path(parsed.path),
                    album_artist=parsed.album_artist,
                    album=parsed.album,
                )
                indexed += 1

                if indexed % self._BATCH_SIZE == 0:
                    connection.commit()
                    connection.execute("BEGIN")

            removed = self._remove_missing_files(connection, seen_paths, roots)
            art_indexed = backfill_missing_album_art(connection, data_dir=self._data_dir)
            prune_orphan_album_art(connection, data_dir=self._data_dir)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        return ScanResult(
            indexed=indexed,
            removed=removed,
            skipped=skipped,
            errors=errors,
            art_indexed=art_indexed,
        )

    def _should_bump_indexed_at(
        self,
        *,
        path_str: str,
        indexed_at_ns: int,
        mtime_ns: int,
        file_mtime_ns: int,
    ) -> bool:
        if indexed_at_ns != 0 and indexed_at_ns != mtime_ns:
            return False
        folder = self._folder_for_path(path_str)
        if folder is not None:
            added_at = self._config.music_folder_added_at.get(folder)
            if added_at is not None and time.time() - added_at <= 30 * 86_400:
                return True
        cutoff_ns = time.time_ns() - 30 * 86_400 * 1_000_000_000
        return indexed_at_ns == 0 and file_mtime_ns >= cutoff_ns

    def _folder_for_path(self, path_str: str) -> str | None:
        for folder in self._config.music_folders:
            folder_resolved = str(Path(folder).resolve())
            if path_str == folder_resolved or path_str.startswith(folder_resolved + os.sep):
                return folder_resolved
        return None

    def _collect_candidates(
        self,
        *,
        roots: list[str],
        progress: ProgressCallback | None = None,
    ) -> list[Path]:
        paths: list[Path] = []
        seen = 0
        for folder in roots:
            root = Path(folder)
            if not root.is_dir():
                continue
            for dirpath, _dirnames, filenames in os.walk(root, followlinks=True):
                for name in filenames:
                    path = Path(dirpath) / name
                    if has_tier1_extension(path):
                        paths.append(path)
                    seen += 1
                    if seen % 5000 == 0:
                        time.sleep(0)
                        if progress is not None:
                            progress(0, 0, f"Discovering files… ({len(paths)} found)")
        paths.sort()
        return paths

    @staticmethod
    def _insert_file(
        connection: sqlite3.Connection,
        parsed: _ParsedTrack,
        *,
        indexed_at_ns: int,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO files(
                path, mtime_ns, size_bytes, indexed_at_ns, codec, duration_sec,
                sample_rate, bit_depth, channels
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                parsed.path,
                parsed.mtime_ns,
                parsed.size_bytes,
                indexed_at_ns,
                parsed.codec,
                parsed.duration_sec,
                parsed.sample_rate,
                parsed.bit_depth,
                parsed.channels,
            ),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _insert_track(connection: sqlite3.Connection, parsed: _ParsedTrack, file_id: int) -> None:
        connection.execute(
            """
            INSERT INTO tracks(
                id, file_id, album_id, title, artist, album_artist, album,
                disc_number, track_number, year, is_synthetic, total_tracks, genre,
                release_type_tag
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ids.track_id(parsed.path),
                file_id,
                parsed.release_id,
                parsed.title,
                parsed.artist,
                parsed.album_artist,
                parsed.album,
                parsed.disc_number,
                parsed.track_number,
                parsed.year,
                1 if parsed.is_synthetic else 0,
                parsed.total_tracks,
                parsed.genre,
                parsed.release_type_tag,
            ),
        )

    @staticmethod
    def _path_under_roots(path_str: str, scan_roots: list[str]) -> bool:
        for root in scan_roots:
            if path_str == root or path_str.startswith(root + os.sep):
                return True
        return False

    @staticmethod
    def _remove_missing_files(
        connection: sqlite3.Connection,
        seen_paths: set[str],
        scan_roots: list[str],
    ) -> int:
        rows = connection.execute("SELECT id, path FROM files").fetchall()
        removed = 0
        for row in rows:
            path_str = row["path"]
            if (
                LibraryScanner._path_under_roots(path_str, scan_roots)
                and path_str not in seen_paths
            ):
                connection.execute("DELETE FROM files WHERE id = ?", (row["id"],))
                removed += 1
        return removed


def _parse_file(path: Path, mtime_ns: int, size_bytes: int) -> _ParsedTrack:
    path_str = str(path.resolve())
    codec = codec_for_path(path)
    tags = _read_tags(path)
    title = tags.get("title") or path.stem
    artist = tags.get("artist") or tags.get("albumartist") or "Unknown Artist"
    album_artist = tags.get("albumartist") or artist
    track_number = tags.get("tracknumber")
    total_tracks = tags.get("totaltracks")
    if total_tracks is None and track_number is not None:
        track_number, total_tracks = _split_track_number(track_number)
    track_id = ids.track_id(path_str)
    album_raw = tags.get("album")
    if _album_tag_missing(album_raw):
        release_id = ids.synthetic_release_id(track_id)
        album = title
        is_synthetic = True
    else:
        album = str(album_raw).strip()
        release_id = ids.release_id(album_artist, album)
        is_synthetic = False
    audio = tags.get("_audio", {})
    return _ParsedTrack(
        path=path_str,
        mtime_ns=mtime_ns,
        size_bytes=size_bytes,
        codec=codec,
        duration_sec=audio.get("duration_sec"),
        sample_rate=audio.get("sample_rate"),
        bit_depth=audio.get("bit_depth"),
        channels=audio.get("channels"),
        title=title,
        artist=artist,
        album_artist=album_artist,
        album=album,
        release_id=release_id,
        is_synthetic=is_synthetic,
        disc_number=tags.get("discnumber"),
        track_number=track_number,
        year=tags.get("year"),
        genre=tags.get("genre"),
        total_tracks=total_tracks,
        release_type_tag=_release_type_tag_from_tags(tags),
    )


def _release_type_tag_from_tags(tags: dict) -> str | None:
    for key in (
        "releasetype",
        "release_type",
        "albumtype",
        "musicbrainz_albumtype",
        "musicbrainz album type",
    ):
        value = tags.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _album_tag_missing(album: str | None) -> bool:
    if album is None:
        return True
    normalized = album.strip().casefold()
    return not normalized or normalized == "unknown album"


def _split_track_number(value: int | str) -> tuple[int | None, int | None]:
    text = str(value)
    if "/" not in text:
        return _parse_int(text), None
    left, _, right = text.partition("/")
    return _parse_int(left), _parse_int(right)


def _read_tags(path: Path) -> dict:
    suffix = path.suffix.casefold()
    if suffix == ".flac":
        return _read_mutagen_flac(path)
    if suffix == ".mp3":
        return _read_mutagen_id3(path)
    if suffix == ".ogg":
        return _read_mutagen_vorbis(path)
    if suffix in {".m4a", ".aac"}:
        return _read_mutagen_mp4(path)
    if suffix in {".wav", ".aiff", ".aif"}:
        return _read_mutagen_generic(path)
    return {}


def _read_mutagen_flac(path: Path) -> dict:
    from mutagen.flac import FLAC

    audio = FLAC(path)
    return _merge_tags(_tags_from_vorbis(audio), _audio_info(audio.info))


def _read_mutagen_id3(path: Path) -> dict:
    from mutagen.easyid3 import EasyID3
    from mutagen.mp3 import MP3

    audio = MP3(path, ID3=EasyID3)
    tags = _tags_from_easyid3(audio.tags or {})
    tags.update(_id3_extra_tags(path))
    tags.update(_audio_info(audio.info))
    return tags


def _id3_extra_tags(path: Path) -> dict:
    """Read MusicBrainz album type and similar TXXX frames not in EasyID3."""
    from mutagen.mp3 import MP3

    try:
        audio = MP3(path)
    except Exception:
        return {}
    if audio.tags is None:
        return {}
    extra: dict = {}
    for frame in audio.tags.values():
        frame_id = getattr(frame, "FrameID", "")
        if frame_id != "TXXX":
            continue
        desc = str(getattr(frame, "desc", "") or "")
        desc_key = desc.strip().casefold()
        if desc_key not in {"musicbrainz album type", "albumtype", "release type"}:
            continue
        text = getattr(frame, "text", None)
        if text:
            extra["musicbrainz_albumtype"] = str(text[0])
            break
    return extra


def _read_mutagen_vorbis(path: Path) -> dict:
    from mutagen.oggvorbis import OggVorbis

    audio = OggVorbis(path)
    return _merge_tags(_tags_from_vorbis(audio), _audio_info(audio.info))


def _read_mutagen_mp4(path: Path) -> dict:
    from mutagen.mp4 import MP4

    audio = MP4(path)
    tags: dict = {"_audio": {}}
    tags.update(_audio_info(audio.info))
    mapping = {
        "\xa9nam": "title",
        "\xa9ART": "artist",
        "aART": "albumartist",
        "\xa9alb": "album",
        "disk": "discnumber",
        "trkn": "tracknumber",
        "\xa9day": "year",
        "\xa9gen": "genre",
    }
    for key, name in mapping.items():
        if key not in audio.tags:
            continue
        value = audio.tags[key]
        if name in {"discnumber", "tracknumber"} and isinstance(value, list) and value:
            tags[name] = int(value[0][0] if isinstance(value[0], tuple) else value[0])
        elif name == "year" and value:
            tags[name] = _parse_year(str(value[0]))
        elif value:
            tags[name] = str(value[0])
    return tags


def _read_mutagen_generic(path: Path) -> dict:
    from mutagen import File

    audio = File(path)
    if audio is None:
        return {}
    if hasattr(audio, "tags") and audio.tags is not None:
        if hasattr(audio.tags, "get"):
            tags = _tags_from_vorbis(audio)
        else:
            tags = _tags_from_easyid3(audio.tags)
    else:
        tags = {}
    tags.update(_audio_info(getattr(audio, "info", None)))
    return tags


def _tags_from_vorbis(audio) -> dict:
    tags: dict = {}
    if not getattr(audio, "tags", None):
        return tags
    raw = audio.tags
    getter = raw.get if hasattr(raw, "get") else lambda _k, _d=None: None
    for key, name in (
        ("title", "title"),
        ("artist", "artist"),
        ("albumartist", "albumartist"),
        ("album", "album"),
        ("discnumber", "discnumber"),
        ("tracknumber", "tracknumber"),
        ("tracktotal", "totaltracks"),
        ("date", "year"),
        ("genre", "genre"),
        ("releasetype", "releasetype"),
        ("release-type", "releasetype"),
        ("musicbrainz_albumtype", "musicbrainz_albumtype"),
        ("musicbrainz album type", "musicbrainz_albumtype"),
    ):
        value = getter(key)
        if not value:
            continue
        if name in {"discnumber", "tracknumber", "totaltracks"}:
            tags[name] = _parse_int(str(value[0]))
        elif name == "year":
            tags[name] = _parse_year(str(value[0]))
        else:
            tags[name] = str(value[0])
    return tags


def _tags_from_easyid3(tags) -> dict:
    result: dict = {}
    for key, name in (
        ("title", "title"),
        ("artist", "artist"),
        ("albumartist", "albumartist"),
        ("album", "album"),
        ("discnumber", "discnumber"),
        ("tracknumber", "tracknumber"),
        ("tracktotal", "totaltracks"),
        ("date", "year"),
        ("genre", "genre"),
        ("releasetype", "releasetype"),
        ("release-type", "releasetype"),
        ("musicbrainz_albumtype", "musicbrainz_albumtype"),
    ):
        if key not in tags:
            continue
        value = tags[key]
        if name in {"discnumber", "tracknumber", "totaltracks"}:
            result[name] = _parse_int(str(value[0]))
        elif name == "year":
            result[name] = _parse_year(str(value[0]))
        else:
            result[name] = str(value[0])
    return result


def _audio_info(info) -> dict:
    if info is None:
        return {"_audio": {}}
    bit_depth = None
    if getattr(info, "bits_per_sample", 0):
        bit_depth = int(info.bits_per_sample)
    return {
        "_audio": {
            "duration_sec": getattr(info, "length", None),
            "sample_rate": getattr(info, "sample_rate", None),
            "bit_depth": bit_depth,
            "channels": getattr(info, "channels", None),
        }
    }


def _merge_tags(tags: dict, audio_tags: dict) -> dict:
    merged = dict(tags)
    merged.update(audio_tags)
    return merged


def _parse_int(value: str) -> int | None:
    digits = "".join(char for char in value if char.isdigit())
    return int(digits) if digits else None


def _parse_year(value: str) -> int | None:
    digits = "".join(char for char in value if char.isdigit())
    if len(digits) >= 4:
        return int(digits[:4])
    return None
