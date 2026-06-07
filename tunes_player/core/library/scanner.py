"""Walk music folders and index Tier 1 audio files."""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tunes_player.core.config import AppConfig
from tunes_player.core.library import ids
from tunes_player.core.library.art_cache import (
    maintain_album_art,
    prune_orphan_album_art,
)
from tunes_player.core.library.db import connect
from tunes_player.core.library.formats import (
    codec_for_path,
    has_tier1_extension,
    is_tier1_path,
)

ProgressCallback = Callable[[int, int, str], None]
_CandidateOutcome = Literal["indexed", "skipped", "error"]


_MAX_RECORDED_FILE_ERRORS = 20
_LOCK_RETRY_ATTEMPTS = 6
_LOCK_RETRY_BASE_DELAY_SEC = 0.15


def _is_locked_error(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


@dataclass(frozen=True, slots=True)
class ScanFileError:
    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class ScanResult:
    indexed: int
    removed: int
    skipped: int
    errors: int
    art_indexed: int = 0
    file_errors: tuple[ScanFileError, ...] = ()
    total_candidates: int = 0


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
        self._resolved_folders: list[str] | None = None

    def scan(
        self,
        *,
        scan_folders: list[str] | None = None,
        progress: ProgressCallback | None = None,
        checkpoint_path: str | None = None,
    ) -> ScanResult:
        del checkpoint_path  # Resume is handled by fast-skipping indexed files.
        last_error: sqlite3.OperationalError | None = None
        for attempt in range(_LOCK_RETRY_ATTEMPTS):
            try:
                return self._scan_once(
                    scan_folders=scan_folders,
                    progress=progress,
                )
            except sqlite3.OperationalError as exc:
                if not _is_locked_error(exc) or attempt == _LOCK_RETRY_ATTEMPTS - 1:
                    raise
                last_error = exc
                time.sleep(_LOCK_RETRY_BASE_DELAY_SEC * (attempt + 1))
        if last_error is not None:
            raise last_error
        raise RuntimeError("library scan retry loop exited without result")

    def scan_changes(
        self,
        *,
        folder: str,
        add_paths: list[str] | None = None,
        remove_paths: list[str] | None = None,
        progress: ProgressCallback | None = None,
    ) -> ScanResult:
        """Index or drop specific paths without walking the whole library folder."""
        last_error: sqlite3.OperationalError | None = None
        for attempt in range(_LOCK_RETRY_ATTEMPTS):
            try:
                return self._scan_changes_once(
                    folder=folder,
                    add_paths=add_paths,
                    remove_paths=remove_paths,
                    progress=progress,
                )
            except sqlite3.OperationalError as exc:
                if not _is_locked_error(exc) or attempt == _LOCK_RETRY_ATTEMPTS - 1:
                    raise
                last_error = exc
                time.sleep(_LOCK_RETRY_BASE_DELAY_SEC * (attempt + 1))
        if last_error is not None:
            raise last_error
        raise RuntimeError("library incremental scan retry loop exited without result")

    def _scan_once(
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
        discovered_paths: set[str] = set()
        seen_paths: set[str] = set()
        indexed = 0
        skipped = 0
        errors = 0
        file_errors: list[ScanFileError] = []
        total = len(candidates)
        last_progress_at = 0.0
        removed = 0
        art_indexed = 0

        connection = connect(self._db_path)
        try:
            connection.execute("BEGIN")
            existing_files = self._load_existing_files_metadata(connection, roots)
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

                outcome, path_str, wrote = self._process_candidate(
                    connection,
                    path,
                    file_errors=file_errors,
                    existing_files=existing_files,
                )
                if path_str is not None:
                    seen_paths.add(path_str)
                    discovered_paths.add(path_str)
                if outcome == "skipped":
                    skipped += 1
                elif outcome == "error":
                    errors += 1
                else:
                    indexed += 1

                if wrote and index % self._BATCH_SIZE == 0:
                    connection.commit()
                    connection.execute("BEGIN")

            connection.commit()
            connection.execute("BEGIN")
            removed = self._remove_missing_files(connection, discovered_paths, roots)
            configured_roots = [
                str(Path(folder).resolve())
                for folder in self._config.music_folders
            ]
            removed += self._purge_files_outside_roots(connection, configured_roots)
            art_added, art_repaired = maintain_album_art(
                connection,
                data_dir=self._data_dir,
            )
            art_indexed = art_added + art_repaired
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
            file_errors=tuple(file_errors),
            total_candidates=total,
        )

    def _scan_changes_once(
        self,
        *,
        folder: str,
        add_paths: list[str] | None = None,
        remove_paths: list[str] | None = None,
        progress: ProgressCallback | None = None,
    ) -> ScanResult:
        root = str(Path(folder).resolve())
        to_add = self._expand_change_paths(add_paths or [])
        to_remove = [str(Path(path).resolve()) for path in (remove_paths or [])]
        indexed = 0
        skipped = 0
        errors = 0
        file_errors: list[ScanFileError] = []
        removed = 0
        art_indexed = 0
        steps: list[tuple[str, Path | str]] = [
            ("remove", path_str) for path_str in to_remove
        ] + [("add", path) for path in to_add]
        total = len(steps)
        last_progress_at = 0.0

        connection = connect(self._db_path)
        try:
            connection.execute("BEGIN")
            for index, (kind, target) in enumerate(steps, start=1):
                if index % self._YIELD_EVERY == 0:
                    time.sleep(0)

                if progress is not None:
                    now = time.monotonic()
                    label = str(target)
                    if (
                        index == 1
                        or index == total
                        or now - last_progress_at >= self._PROGRESS_INTERVAL_SEC
                    ):
                        progress(index, total, label)
                        last_progress_at = now

                if kind == "remove":
                    removed += self._remove_paths(connection, [str(target)])
                    continue

                if not isinstance(target, Path):
                    target = Path(str(target))
                if not is_tier1_path(target):
                    skipped += 1
                    continue

                outcome, _path_str, wrote = self._process_candidate(
                    connection,
                    target,
                    file_errors=file_errors,
                )
                if outcome == "skipped":
                    skipped += 1
                elif outcome == "error":
                    errors += 1
                else:
                    indexed += 1
                if wrote and index % self._BATCH_SIZE == 0:
                    connection.commit()
                    connection.execute("BEGIN")

            art_added, art_repaired = maintain_album_art(
                connection,
                data_dir=self._data_dir,
            )
            art_indexed = art_added + art_repaired
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
            file_errors=tuple(file_errors),
            total_candidates=total,
        )

    @staticmethod
    def _record_file_error(
        file_errors: list[ScanFileError],
        path_str: str,
        reason: str,
    ) -> None:
        if len(file_errors) >= _MAX_RECORDED_FILE_ERRORS:
            return
        text = reason.strip() or "unknown error"
        file_errors.append(ScanFileError(path_str, text))

    def _canonical_path_str(self, path: Path) -> str:
        try:
            return str(path.resolve())
        except OSError:
            return str(path.absolute())

    def _lookup_existing_file(
        self,
        path: Path,
        existing_files: dict[str, sqlite3.Row] | None,
        connection: sqlite3.Connection,
        *,
        resolved_path: str | None = None,
    ) -> tuple[str, sqlite3.Row | None]:
        path_candidates: list[str] = []
        if resolved_path is not None:
            path_candidates.append(resolved_path)
        try:
            resolved = str(path.resolve())
            if resolved not in path_candidates:
                path_candidates.append(resolved)
        except OSError:
            pass
        absolute = str(path.absolute())
        if absolute not in path_candidates:
            path_candidates.append(absolute)

        for path_str in path_candidates:
            row: sqlite3.Row | None = None
            if existing_files is not None:
                row = existing_files.get(path_str)
                if row is None:
                    for key, candidate in existing_files.items():
                        if os.path.normcase(key) == os.path.normcase(path_str):
                            row = candidate
                            path_str = str(key)
                            break
            else:
                row = connection.execute(
                    """
                    SELECT id, path, mtime_ns, size_bytes, indexed_at_ns
                    FROM files WHERE path = ?
                    """,
                    (path_str,),
                ).fetchone()
            if row is not None:
                return path_str, row

        return (path_candidates[0] if path_candidates else self._canonical_path_str(path)), None

    @staticmethod
    def _file_metadata_unchanged(existing: sqlite3.Row, stat: os.stat_result) -> bool:
        if int(existing["size_bytes"]) != stat.st_size:
            return False
        existing_ns = int(existing["mtime_ns"])
        file_ns = stat.st_mtime_ns
        if existing_ns == file_ns:
            return True
        # NFS and other remote filesystems often expose only second precision.
        return existing_ns // 1_000_000_000 == file_ns // 1_000_000_000

    def _process_candidate(
        self,
        connection: sqlite3.Connection,
        path: Path,
        *,
        file_errors: list[ScanFileError],
        existing_files: dict[str, sqlite3.Row] | None = None,
    ) -> tuple[_CandidateOutcome, str | None, bool]:
        if not is_tier1_path(path):
            return "skipped", None, False

        path_str, existing = self._lookup_existing_file(path, existing_files, connection)
        if existing is not None and not self._should_bump_indexed_at(
            path_str=path_str,
            indexed_at_ns=int(existing["indexed_at_ns"]),
            mtime_ns=int(existing["mtime_ns"]),
            file_mtime_ns=int(existing["mtime_ns"]),
        ):
            return "skipped", path_str, False

        try:
            stat = path.stat()
        except OSError as exc:
            self._record_file_error(
                file_errors,
                path_str,
                f"could not read file ({exc})",
            )
            return "error", path_str, False

        if existing is None:
            path_str, existing = self._lookup_existing_file(
                path,
                existing_files,
                connection,
                resolved_path=path_str,
            )

        if (
            existing is not None
            and self._file_metadata_unchanged(existing, stat)
        ):
            wrote = False
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
                wrote = True
            return "skipped", path_str, wrote

        try:
            parsed = _parse_file(path, stat.st_mtime_ns, stat.st_size)
        except Exception as exc:
            reason = str(exc).strip() or type(exc).__name__
            self._record_file_error(file_errors, path_str, reason)
            return "error", path_str, False

        last_error: sqlite3.OperationalError | None = None
        for attempt in range(_LOCK_RETRY_ATTEMPTS):
            connection.execute("SAVEPOINT index_file")
            try:
                track_pk = ids.track_id(path_str)
                connection.execute("DELETE FROM tracks WHERE id = ?", (track_pk,))
                connection.execute("DELETE FROM files WHERE path = ?", (path_str,))
                file_id = self._insert_file(connection, parsed, indexed_at_ns=time.time_ns())
                self._insert_track(connection, parsed, file_id)
                connection.execute("RELEASE SAVEPOINT index_file")
                return "indexed", path_str, True
            except sqlite3.OperationalError as exc:
                try:
                    connection.execute("ROLLBACK TO SAVEPOINT index_file")
                    connection.execute("RELEASE SAVEPOINT index_file")
                except sqlite3.OperationalError:
                    pass
                if _is_locked_error(exc) and attempt < _LOCK_RETRY_ATTEMPTS - 1:
                    last_error = exc
                    time.sleep(_LOCK_RETRY_BASE_DELAY_SEC * (attempt + 1))
                    continue
                reason = str(exc).strip() or type(exc).__name__
                self._record_file_error(file_errors, path_str, reason)
                return "error", path_str, False
            except Exception as exc:
                try:
                    connection.execute("ROLLBACK TO SAVEPOINT index_file")
                    connection.execute("RELEASE SAVEPOINT index_file")
                except sqlite3.OperationalError:
                    pass
                reason = str(exc).strip() or type(exc).__name__
                self._record_file_error(file_errors, path_str, reason)
                return "error", path_str, False

        if last_error is not None:
            self._record_file_error(file_errors, path_str, str(last_error))
        return "error", path_str, False

    def purge_folder(self, folder: str) -> int:
        """Remove all indexed files under *folder* from the library database."""
        root = str(Path(folder).resolve())
        last_error: sqlite3.OperationalError | None = None
        for attempt in range(6):
            connection = connect(self._db_path)
            try:
                connection.execute("BEGIN IMMEDIATE")
                removed = self._purge_files_under_roots(connection, [root])
                prune_orphan_album_art(connection, data_dir=self._data_dir)
                connection.commit()
                return removed
            except sqlite3.OperationalError as exc:
                connection.rollback()
                if not _is_locked_error(exc) or attempt == 5:
                    raise
                last_error = exc
                time.sleep(0.15 * (attempt + 1))
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        if last_error is not None:
            raise last_error
        return 0

    def purge_unconfigured_folders(self) -> int:
        """Remove indexed files whose path is outside configured music folders."""
        roots = [
            str(Path(folder).resolve())
            for folder in self._config.music_folders
        ]
        last_error: sqlite3.OperationalError | None = None
        for attempt in range(6):
            connection = connect(self._db_path)
            try:
                connection.execute("BEGIN IMMEDIATE")
                removed = self._purge_files_outside_roots(connection, roots)
                prune_orphan_album_art(connection, data_dir=self._data_dir)
                connection.commit()
                return removed
            except sqlite3.OperationalError as exc:
                connection.rollback()
                if not _is_locked_error(exc) or attempt == 5:
                    raise
                last_error = exc
                time.sleep(0.15 * (attempt + 1))
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        if last_error is not None:
            raise last_error
        return 0

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
        for folder in self._resolved_music_folders():
            if path_str == folder or path_str.startswith(folder + os.sep):
                return folder
        return None

    def _resolved_music_folders(self) -> list[str]:
        if self._resolved_folders is None:
            self._resolved_folders = [
                str(Path(folder).resolve())
                for folder in self._config.music_folders
            ]
        return self._resolved_folders

    def _collect_candidates(
        self,
        *,
        roots: list[str],
        progress: ProgressCallback | None = None,
    ) -> list[Path]:
        paths: list[Path] = []
        seen = 0
        last_progress_at = 0.0
        for folder in roots:
            root = Path(folder)
            if not root.is_dir():
                continue
            for dirpath, _dirnames, filenames in os.walk(root, followlinks=True):
                for name in filenames:
                    candidate = Path(dirpath) / name
                    if not has_tier1_extension(candidate):
                        seen += 1
                        continue
                    paths.append(candidate)
                    seen += 1
                    if progress is not None:
                        now = time.monotonic()
                        if (
                            seen == 1
                            or seen % 5000 == 0
                            or now - last_progress_at >= self._PROGRESS_INTERVAL_SEC
                        ):
                            time.sleep(0)
                            progress(
                                0,
                                0,
                                f"Discovering files… ({len(paths):,} found)",
                            )
                            last_progress_at = now
        if progress is not None and paths:
            progress(0, 0, f"Discovering files… ({len(paths):,} found)")
        if progress is not None and len(paths) >= 10_000:
            progress(0, 0, f"Discovering files… ({len(paths):,} found, sorting…)")
        paths.sort(key=str)
        if progress is not None and len(paths) >= 10_000:
            progress(
                0,
                0,
                f"Discovering files… ({len(paths):,} found, preparing scan…)",
            )
        return paths

    @staticmethod
    def _load_existing_files_metadata(
        connection: sqlite3.Connection,
        roots: list[str],
    ) -> dict[str, sqlite3.Row]:
        by_path: dict[str, sqlite3.Row] = {}
        for root in roots:
            rows = connection.execute(
                """
                SELECT id, path, mtime_ns, size_bytes, indexed_at_ns
                FROM files
                WHERE path = ? OR path LIKE ?
                """,
                (root, root + os.sep + "%"),
            ).fetchall()
            for row in rows:
                by_path[str(row["path"])] = row
        return by_path

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

    def _expand_change_paths(self, paths: list[str]) -> list[Path]:
        candidates: list[Path] = []
        seen: set[str] = set()
        for raw in paths:
            path = Path(raw)
            if path.is_file():
                if has_tier1_extension(path):
                    path_str = str(path.resolve())
                    if path_str not in seen:
                        seen.add(path_str)
                        candidates.append(path.resolve())
                continue
            if not path.is_dir():
                continue
            for dirpath, _dirnames, filenames in os.walk(path, followlinks=True):
                for name in filenames:
                    candidate = Path(dirpath) / name
                    if not has_tier1_extension(candidate):
                        continue
                    path_str = str(candidate.resolve())
                    if path_str in seen:
                        continue
                    seen.add(path_str)
                    candidates.append(candidate.resolve())
        candidates.sort()
        return candidates

    @staticmethod
    def _remove_paths(connection: sqlite3.Connection, paths: list[str]) -> int:
        removed = 0
        for raw in paths:
            path_str = str(Path(raw).resolve())
            prefix = path_str + os.sep
            rows = connection.execute(
                """
                SELECT id FROM files
                WHERE path = ? OR path LIKE ?
                """,
                (path_str, prefix + "%"),
            ).fetchall()
            for row in rows:
                connection.execute("DELETE FROM files WHERE id = ?", (int(row["id"]),))
                removed += 1
        return removed

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
        return LibraryScanner._purge_files_under_roots(
            connection,
            scan_roots,
            exclude_paths=seen_paths,
        )

    @staticmethod
    def _purge_files_under_roots(
        connection: sqlite3.Connection,
        roots: list[str],
        *,
        exclude_paths: set[str] | None = None,
    ) -> int:
        if exclude_paths is None:
            removed = 0
            for root in roots:
                cursor = connection.execute(
                    "DELETE FROM files WHERE path = ? OR path LIKE ?",
                    (root, root + os.sep + "%"),
                )
                removed += int(cursor.rowcount)
            return removed

        removed = 0
        for root in roots:
            rows = connection.execute(
                """
                SELECT id, path FROM files
                WHERE path = ? OR path LIKE ?
                """,
                (root, root + os.sep + "%"),
            ).fetchall()
            for row in rows:
                path_str = row["path"]
                if path_str in exclude_paths:
                    continue
                connection.execute("DELETE FROM files WHERE id = ?", (row["id"],))
                removed += 1
        return removed

    @staticmethod
    def purge_files_outside_roots(
        connection: sqlite3.Connection,
        roots: list[str],
    ) -> int:
        if not roots:
            cursor = connection.execute("DELETE FROM files")
            return int(cursor.rowcount)
        clauses: list[str] = []
        params: list[str] = []
        for root in roots:
            clauses.append("(path = ? OR path LIKE ?)")
            params.extend((root, root + os.sep + "%"))
        where_under = " OR ".join(clauses)
        cursor = connection.execute(
            f"DELETE FROM files WHERE NOT ({where_under})",
            params,
        )
        return int(cursor.rowcount)

    @staticmethod
    def _purge_files_outside_roots(
        connection: sqlite3.Connection,
        roots: list[str],
    ) -> int:
        return LibraryScanner.purge_files_outside_roots(connection, roots)


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
    if audio.tags is not None:
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
                tags["musicbrainz_albumtype"] = str(text[0])
                break
    tags.update(_audio_info(audio.info))
    return tags


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
