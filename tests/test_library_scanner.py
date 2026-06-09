"""Library scanner scoped folder tests."""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from tunes_player.core.config import AppConfig
from tunes_player.core.library import ids
from tunes_player.core.library.db import connect
from tunes_player.core.library.scanner import (
    ExistingFileIndex,
    LibraryScanner,
    ScanResult,
    _ParsedTrack,
    _UnsupportedTier1Path,
    _parse_file,
)


class LibraryScannerScopedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)
        self._folder_a = self._root / "music_a"
        self._folder_b = self._root / "music_b"
        self._folder_a.mkdir()
        self._folder_b.mkdir()
        (self._folder_a / "track_a.flac").write_bytes(b"")
        (self._folder_b / "track_b.flac").write_bytes(b"")

        self._db_path = self._root / "library.db"
        self._config = AppConfig(
            music_folders=[
                str(self._folder_a.resolve()),
                str(self._folder_b.resolve()),
            ],
        )
        self._scanner = LibraryScanner(db_path=self._db_path, config=self._config)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_collect_candidates_scoped_to_one_folder(self) -> None:
        roots = [str(self._folder_a.resolve())]
        discovery = self._scanner._collect_candidates(roots=roots)
        self.assertEqual(len(discovery), 1)
        self.assertEqual(discovery[0].name, "track_a.flac")
        self.assertTrue(str(discovery[0].resolve()).startswith(str(self._folder_a.resolve())))

    def test_remove_missing_files_scoped_to_scan_roots(self) -> None:
        path_a = str((self._folder_a / "gone.flac").resolve())
        path_b = str((self._folder_b / "stay.flac").resolve())
        connection = connect(self._db_path)
        try:
            connection.execute(
                "INSERT INTO files(path, mtime_ns, size_bytes, indexed_at_ns) VALUES (?, ?, ?, ?)",
                (path_a, 1, 1, 1),
            )
            connection.execute(
                "INSERT INTO files(path, mtime_ns, size_bytes, indexed_at_ns) VALUES (?, ?, ?, ?)",
                (path_b, 1, 1, 1),
            )
            connection.commit()

            removed = LibraryScanner._remove_missing_files(
                connection,
                seen_paths=set(),
                scan_roots=[str(self._folder_a.resolve())],
            )
            connection.commit()

            remaining = {
                row["path"]
                for row in connection.execute("SELECT path FROM files").fetchall()
            }
        finally:
            connection.close()

        self.assertEqual(removed, 1)
        self.assertNotIn(path_a, remaining)
        self.assertIn(path_b, remaining)

    def test_scan_one_folder_does_not_remove_other_folder_index(self) -> None:
        path_b = str((self._folder_b / "track_b.flac").resolve())
        connection = connect(self._db_path)
        try:
            connection.execute(
                "INSERT INTO files(path, mtime_ns, size_bytes, indexed_at_ns) VALUES (?, ?, ?, ?)",
                (path_b, 1, 1, 1),
            )
            connection.commit()
        finally:
            connection.close()

        self._scanner.scan(scan_folders=[str(self._folder_a.resolve())])

        connection = connect(self._db_path)
        try:
            remaining = {
                row["path"]
                for row in connection.execute("SELECT path FROM files").fetchall()
            }
        finally:
            connection.close()

        self.assertIn(path_b, remaining)

    def test_track_ids_differ_for_case_only_path_difference(self) -> None:
        self.assertNotEqual(
            ids.track_id("/tmp/Music/song.flac"),
            ids.track_id("/tmp/music/song.flac"),
        )

    def test_purge_folder_removes_indexed_files_and_tracks(self) -> None:
        path_a = str((self._folder_a / "track_a.flac").resolve())
        path_b = str((self._folder_b / "track_b.flac").resolve())
        connection = connect(self._db_path)
        try:
            connection.execute(
                "INSERT INTO files(path, mtime_ns, size_bytes, indexed_at_ns) VALUES (?, ?, ?, ?)",
                (path_a, 1, 1, 1),
            )
            file_a_id = connection.execute(
                "SELECT id FROM files WHERE path = ?",
                (path_a,),
            ).fetchone()["id"]
            connection.execute(
                """
                INSERT INTO tracks(
                    id, file_id, album_id, title, artist, album_artist, album, is_synthetic
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    ids.track_id(path_a),
                    file_a_id,
                    ids.release_id("Artist", "Album A"),
                    "Track A",
                    "Artist",
                    "Artist",
                    "Album A",
                ),
            )
            connection.execute(
                "INSERT INTO files(path, mtime_ns, size_bytes, indexed_at_ns) VALUES (?, ?, ?, ?)",
                (path_b, 1, 1, 1),
            )
            file_b_id = connection.execute(
                "SELECT id FROM files WHERE path = ?",
                (path_b,),
            ).fetchone()["id"]
            connection.execute(
                """
                INSERT INTO tracks(
                    id, file_id, album_id, title, artist, album_artist, album, is_synthetic
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    ids.track_id(path_b),
                    file_b_id,
                    ids.release_id("Artist", "Album B"),
                    "Track B",
                    "Artist",
                    "Artist",
                    "Album B",
                ),
            )
            connection.commit()
        finally:
            connection.close()

        removed = self._scanner.purge_folder(str(self._folder_a.resolve()))

        connection = connect(self._db_path)
        try:
            paths = {
                row["path"]
                for row in connection.execute("SELECT path FROM files").fetchall()
            }
            track_ids = {
                row["id"]
                for row in connection.execute("SELECT id FROM tracks").fetchall()
            }
        finally:
            connection.close()

        self.assertEqual(removed, 1)
        self.assertNotIn(path_a, paths)
        self.assertIn(path_b, paths)
        self.assertNotIn(ids.track_id(path_a), track_ids)
        self.assertIn(ids.track_id(path_b), track_ids)

    def test_purge_unconfigured_folders_removes_orphaned_roots(self) -> None:
        path_a = str((self._folder_a / "track_a.flac").resolve())
        path_b = str((self._folder_b / "track_b.flac").resolve())
        connection = connect(self._db_path)
        try:
            for path in (path_a, path_b):
                connection.execute(
                    "INSERT INTO files(path, mtime_ns, size_bytes, indexed_at_ns) VALUES (?, ?, ?, ?)",
                    (path, 1, 1, 1),
                )
            connection.commit()
        finally:
            connection.close()

        config = AppConfig(
            music_folders=[str(self._folder_a.resolve())],
            music_folder_added_at={str(self._folder_a.resolve()): 1.0},
        )
        scanner = LibraryScanner(db_path=self._db_path, config=config)
        removed = scanner.purge_unconfigured_folders()

        connection = connect(self._db_path)
        try:
            paths = {
                row["path"]
                for row in connection.execute("SELECT path FROM files").fetchall()
            }
        finally:
            connection.close()

        self.assertEqual(removed, 1)
        self.assertIn(path_a, paths)
        self.assertNotIn(path_b, paths)

    def test_scan_indexes_paths_that_old_casefold_ids_would_collide(self) -> None:
        collision_root = self._root / "collision"
        dir_a = collision_root / "Music"
        dir_b = collision_root / "music"
        dir_a.mkdir(parents=True)
        dir_b.mkdir()
        file_a = dir_a / "song.flac"
        file_b = dir_b / "song.flac"
        file_a.write_bytes(b"")
        file_b.write_bytes(b"")

        def fake_parse(path: Path, mtime_ns: int, size_bytes: int) -> _ParsedTrack:
            path_str = str(path.resolve())
            return _ParsedTrack(
                path=path_str,
                mtime_ns=mtime_ns,
                size_bytes=size_bytes,
                codec="flac",
                duration_sec=None,
                sample_rate=None,
                bit_depth=None,
                channels=None,
                title=path.stem,
                artist="Artist",
                album_artist="Artist",
                album="Album",
                release_id=ids.release_id("Artist", "Album"),
                is_synthetic=False,
                disc_number=None,
                track_number=None,
                year=None,
                genre=None,
                total_tracks=None,
                release_type_tag=None,
            )

        config = AppConfig(music_folders=[str(collision_root.resolve())])
        scanner = LibraryScanner(db_path=self._db_path, config=config)
        with (
            patch("tunes_player.core.library.scanner._parse_file", side_effect=fake_parse),
            patch("tunes_player.core.library.scanner.maintain_album_art", return_value=(0, 0)),
        ):
            result = scanner.scan(scan_folders=[str(collision_root.resolve())])

        connection = connect(self._db_path)
        try:
            paths = {
                row["path"]
                for row in connection.execute("SELECT path FROM files").fetchall()
            }
            track_count = connection.execute("SELECT COUNT(*) AS n FROM tracks").fetchone()["n"]
        finally:
            connection.close()

        self.assertEqual(result.errors, 0)
        self.assertEqual(result.indexed, 2)
        self.assertEqual(len(paths), 2)
        self.assertEqual(track_count, 2)

    def test_scan_records_file_parse_errors(self) -> None:
        scan_root = self._folder_a / "error_case"
        scan_root.mkdir()
        bad = scan_root / "broken.flac"
        bad.write_bytes(b"not flac")
        good = scan_root / "good.flac"
        good.write_bytes(b"")

        def fake_parse(path: Path, mtime_ns: int, size_bytes: int) -> _ParsedTrack:
            if path.name == "broken.flac":
                raise ValueError("invalid FLAC header")
            path_str = str(path.resolve())
            return _ParsedTrack(
                path=path_str,
                mtime_ns=mtime_ns,
                size_bytes=size_bytes,
                codec="flac",
                duration_sec=None,
                sample_rate=None,
                bit_depth=None,
                channels=None,
                title=path.stem,
                artist="Artist",
                album_artist="Artist",
                album="Album",
                release_id=ids.release_id("Artist", "Album"),
                is_synthetic=False,
                disc_number=None,
                track_number=None,
                year=None,
                genre=None,
                total_tracks=None,
                release_type_tag=None,
            )

        with (
            patch("tunes_player.core.library.scanner._parse_file", side_effect=fake_parse),
            patch("tunes_player.core.library.scanner.maintain_album_art", return_value=(0, 0)),
        ):
            result = self._scanner.scan(scan_folders=[str(scan_root.resolve())])

        self.assertEqual(result.errors, 1)
        self.assertEqual(result.indexed, 1)
        self.assertEqual(len(result.file_errors), 1)
        self.assertIn("broken.flac", result.file_errors[0].path)
        self.assertEqual(result.file_errors[0].reason, "invalid FLAC header")

    def test_scan_survives_embedded_album_art_during_indexing(self) -> None:
        scan_root = self._folder_a / "art_case"
        scan_root.mkdir()
        track = scan_root / "with_art.flac"
        track.write_bytes(b"")

        def fake_parse(path: Path, mtime_ns: int, size_bytes: int) -> _ParsedTrack:
            path_str = str(path.resolve())
            return _ParsedTrack(
                path=path_str,
                mtime_ns=mtime_ns,
                size_bytes=size_bytes,
                codec="flac",
                duration_sec=None,
                sample_rate=None,
                bit_depth=None,
                channels=None,
                title=path.stem,
                artist="Artist",
                album_artist="Artist",
                album="Album",
                release_id=ids.release_id("Artist", "Album"),
                is_synthetic=False,
                disc_number=None,
                track_number=None,
                year=None,
                genre=None,
                total_tracks=None,
                release_type_tag=None,
            )

        with (
            patch("tunes_player.core.library.scanner._parse_file", side_effect=fake_parse),
            patch(
                "tunes_player.core.library.art_cache.extract_embedded_art",
                return_value=(b"jpeg-bytes", "image/jpeg"),
            ),
        ):
            result = self._scanner.scan(scan_folders=[str(scan_root.resolve())])

        self.assertEqual(result.errors, 0)
        self.assertEqual(result.indexed, 1)
        self.assertEqual(result.art_indexed, 1)

        connection = connect(self._db_path)
        try:
            album_id = ids.release_id("Artist", "Album")
            row = connection.execute(
                "SELECT 1 FROM album_art WHERE album_id = ?",
                (album_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)

    def test_scan_continues_after_indexing_failure(self) -> None:
        scan_root = self._folder_a / "index_error_case"
        scan_root.mkdir()
        bad = scan_root / "bad.flac"
        bad.write_bytes(b"")
        good = scan_root / "good.flac"
        good.write_bytes(b"")

        def fake_parse(path: Path, mtime_ns: int, size_bytes: int) -> _ParsedTrack:
            path_str = str(path.resolve())
            return _ParsedTrack(
                path=path_str,
                mtime_ns=mtime_ns,
                size_bytes=size_bytes,
                codec="flac",
                duration_sec=None,
                sample_rate=None,
                bit_depth=None,
                channels=None,
                title=path.stem,
                artist="Artist",
                album_artist="Artist",
                album="Album",
                release_id=ids.release_id("Artist", "Album"),
                is_synthetic=False,
                disc_number=None,
                track_number=None,
                year=None,
                genre=None,
                total_tracks=None,
                release_type_tag=None,
            )

        original_insert_track = LibraryScanner._insert_track

        def failing_insert_track(
            connection,
            parsed: _ParsedTrack,
            file_id: int,
        ) -> None:
            if parsed.path.endswith("bad.flac"):
                raise RuntimeError("database write failed")
            original_insert_track(connection, parsed, file_id)

        with (
            patch("tunes_player.core.library.scanner._parse_file", side_effect=fake_parse),
            patch.object(LibraryScanner, "_insert_track", side_effect=failing_insert_track),
            patch("tunes_player.core.library.scanner.maintain_album_art", return_value=(0, 0)),
        ):
            result = self._scanner.scan(scan_folders=[str(scan_root.resolve())])

        self.assertEqual(result.errors, 1)
        self.assertEqual(result.indexed, 1)
        self.assertEqual(len(result.file_errors), 1)
        self.assertIn("bad.flac", result.file_errors[0].path)
        self.assertEqual(result.file_errors[0].reason, "database write failed")

    def test_scan_commits_after_every_batch_of_processed_files(self) -> None:
        candidates = [Path(f"/tmp/fake_{index}.flac") for index in range(120)]
        commits: list[None] = []
        real_connect = connect

        class _TrackingConnection:
            def __init__(self, connection: sqlite3.Connection) -> None:
                self._connection = connection

            def commit(self) -> None:
                commits.append(None)
                self._connection.commit()

            def __getattr__(self, name: str):
                return getattr(self._connection, name)

        def tracking_connect(db_path: Path):
            return _TrackingConnection(real_connect(db_path))

        with (
            patch("tunes_player.core.library.scanner.connect", side_effect=tracking_connect),
            patch.object(LibraryScanner, "_collect_candidates", return_value=candidates),
            patch.object(
                LibraryScanner,
                "_process_candidate",
                return_value=("indexed", "/tmp/fake_0.flac", True, False),
            ),
            patch(
                "tunes_player.core.library.scanner.maintain_album_art",
                return_value=(0, 0),
            ),
        ):
            self._scanner.scan(scan_folders=[str(self._folder_a.resolve())])

        self.assertGreaterEqual(len(commits), 2)

    def test_scan_changes_indexes_added_file_and_removes_deleted_file(self) -> None:
        added = self._folder_a / "new_track.flac"
        added.write_bytes(b"")
        stale = str((self._folder_a / "stale.flac").resolve())
        connection = connect(self._db_path)
        try:
            connection.execute(
                "INSERT INTO files(path, mtime_ns, size_bytes, indexed_at_ns) VALUES (?, ?, ?, ?)",
                (stale, 1, 1, 1),
            )
            connection.commit()
        finally:
            connection.close()

        with (
            patch("tunes_player.core.library.scanner._parse_file") as parse_file,
            patch("tunes_player.core.library.scanner.maintain_album_art", return_value=(0, 0)),
        ):
            parse_file.return_value = _ParsedTrack(
                path=str(added.resolve()),
                mtime_ns=1,
                size_bytes=1,
                codec="flac",
                duration_sec=None,
                sample_rate=None,
                bit_depth=None,
                channels=None,
                title="New",
                artist="Artist",
                album_artist="Artist",
                album="Album",
                release_id=ids.release_id("Artist", "Album"),
                is_synthetic=False,
                disc_number=None,
                track_number=None,
                year=None,
                genre=None,
                total_tracks=None,
                release_type_tag=None,
            )
            result = self._scanner.scan_changes(
                folder=str(self._folder_a.resolve()),
                add_paths=[str(added.resolve())],
                remove_paths=[stale],
            )

        self.assertEqual(result.indexed, 1)
        self.assertEqual(result.removed, 1)

        connection = connect(self._db_path)
        try:
            paths = {
                row["path"]
                for row in connection.execute("SELECT path FROM files").fetchall()
            }
        finally:
            connection.close()

        self.assertIn(str(added.resolve()), paths)
        self.assertNotIn(stale, paths)

    def test_scan_retries_on_database_locked(self) -> None:
        attempts = {"count": 0}

        def flaky_scan_once(**_kwargs: object) -> ScanResult:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise sqlite3.OperationalError("database is locked")
            return ScanResult(indexed=0, removed=0, skipped=0, errors=0)

        with patch.object(LibraryScanner, "_scan_once", side_effect=flaky_scan_once):
            result = self._scanner.scan(scan_folders=[str(self._folder_a.resolve())])

        self.assertEqual(attempts["count"], 2)
        self.assertEqual(result.indexed, 0)

    def test_rescan_skips_already_indexed_files(self) -> None:
        scan_root = self._folder_a / "resume"
        scan_root.mkdir()
        file_a = scan_root / "a.flac"
        file_b = scan_root / "b.flac"
        file_c = scan_root / "c.flac"
        file_a.write_bytes(b"a")
        file_b.write_bytes(b"b")
        file_c.write_bytes(b"c")

        def fake_parse(path: Path, mtime_ns: int, size_bytes: int) -> _ParsedTrack:
            path_str = str(path.resolve())
            return _ParsedTrack(
                path=path_str,
                mtime_ns=mtime_ns,
                size_bytes=size_bytes,
                codec="flac",
                duration_sec=None,
                sample_rate=None,
                bit_depth=None,
                channels=None,
                title=path.stem,
                artist="Artist",
                album_artist="Artist",
                album="Album",
                release_id=ids.release_id("Artist", "Album"),
                is_synthetic=False,
                disc_number=None,
                track_number=None,
                year=None,
                genre=None,
                total_tracks=None,
                release_type_tag=None,
            )

        config = AppConfig(music_folders=[str(scan_root.resolve())])
        scanner = LibraryScanner(db_path=self._db_path, config=config)
        with (
            patch("tunes_player.core.library.scanner._parse_file", side_effect=fake_parse),
            patch("tunes_player.core.library.scanner.maintain_album_art", return_value=(0, 0)),
        ):
            file_c.unlink()
            first = scanner.scan(scan_folders=[str(scan_root.resolve())])
            file_c.write_bytes(b"c")
            result = scanner.scan(
                scan_folders=[str(scan_root.resolve())],
                checkpoint_path=str(file_b.resolve()),
            )

        self.assertEqual(first.indexed, 2)
        self.assertEqual(result.indexed, 1)
        self.assertEqual(result.skipped, 2)
        self.assertEqual(result.total_candidates, 3)

        connection = connect(self._db_path)
        try:
            paths = {
                row["path"]
                for row in connection.execute("SELECT path FROM files").fetchall()
            }
        finally:
            connection.close()
        self.assertEqual(
            paths,
            {
                str(file_a.resolve()),
                str(file_b.resolve()),
                str(file_c.resolve()),
            },
        )

    def test_interrupted_scan_skips_missing_file_cleanup(self) -> None:
        scan_root = self._folder_a / "interrupt"
        scan_root.mkdir()
        present = scan_root / "present.flac"
        present.write_bytes(b"")
        stale = str((scan_root / "stale.flac").resolve())
        connection = connect(self._db_path)
        try:
            connection.execute(
                "INSERT INTO files(path, mtime_ns, size_bytes, indexed_at_ns) VALUES (?, ?, ?, ?)",
                (stale, 1, 1, 1),
            )
            connection.commit()
        finally:
            connection.close()

        calls = {"count": 0}
        original_process = LibraryScanner._process_candidate

        def flaky_process(self, connection, path, *, file_errors, existing_files=None):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("scan interrupted")
            return original_process(
                self,
                connection,
                path,
                file_errors=file_errors,
                existing_files=existing_files,
            )

        config = AppConfig(music_folders=[str(scan_root.resolve())])
        scanner = LibraryScanner(db_path=self._db_path, config=config)
        with (
            patch.object(LibraryScanner, "_process_candidate", flaky_process),
            patch.object(LibraryScanner, "_remove_missing_files") as remove_missing,
            patch("tunes_player.core.library.scanner.maintain_album_art", return_value=(0, 0)),
        ):
            with self.assertRaises(RuntimeError):
                scanner.scan(scan_folders=[str(scan_root.resolve())])
            remove_missing.assert_not_called()

        connection = connect(self._db_path)
        try:
            paths = {
                row["path"]
                for row in connection.execute("SELECT path FROM files").fetchall()
            }
        finally:
            connection.close()
        self.assertIn(stale, paths)

    def test_process_candidate_skips_indexed_file_with_stat(self) -> None:
        track = self._folder_a / "indexed.flac"
        track.write_bytes(b"data")
        path_str = str(track.resolve())
        stat = track.stat()
        connection = connect(self._db_path)
        try:
            connection.execute(
                "INSERT INTO files(path, mtime_ns, size_bytes, indexed_at_ns) VALUES (?, ?, ?, ?)",
                (path_str, stat.st_mtime_ns, stat.st_size, time.time_ns()),
            )
            connection.commit()
            existing = LibraryScanner._load_existing_files_metadata(
                connection,
                [str(self._folder_a.resolve())],
            )
            with patch("tunes_player.core.library.scanner._parse_file") as parse_file:
                outcome, result_path, wrote, _art_added = self._scanner._process_candidate(
                    connection,
                    track,
                    file_errors=[],
                    existing_files=existing,
                )
        finally:
            connection.close()

        parse_file.assert_not_called()
        self.assertEqual(outcome, "skipped")
        self.assertEqual(result_path, path_str)
        self.assertFalse(wrote)

    def test_process_candidate_skips_when_mtime_differs_only_in_subseconds(self) -> None:
        track = self._folder_a / "nfs-ish.flac"
        track.write_bytes(b"data")
        path_str = str(track.resolve())
        stat = track.stat()
        connection = connect(self._db_path)
        try:
            connection.execute(
                "INSERT INTO files(path, mtime_ns, size_bytes, indexed_at_ns) VALUES (?, ?, ?, ?)",
                (path_str, stat.st_mtime_ns + 1, stat.st_size, time.time_ns()),
            )
            connection.commit()
            existing = LibraryScanner._load_existing_files_metadata(
                connection,
                [str(self._folder_a.resolve())],
            )
            with patch("tunes_player.core.library.scanner._parse_file") as parse_file:
                outcome, result_path, wrote, _art_added = self._scanner._process_candidate(
                    connection,
                    track,
                    file_errors=[],
                    existing_files=existing,
                )
        finally:
            connection.close()

        parse_file.assert_not_called()
        self.assertEqual(outcome, "skipped")
        self.assertEqual(result_path, path_str)
        self.assertFalse(wrote)

    def test_collect_candidates_reports_time_based_progress(self) -> None:
        scan_root = self._folder_a / "discover"
        scan_root.mkdir()
        for index in range(12):
            (scan_root / f"track_{index:02d}.flac").write_bytes(b"")

        progress_calls: list[tuple[int, int, str]] = []

        def progress(current: int, total: int, path: str) -> None:
            progress_calls.append((current, total, path))

        with patch.object(LibraryScanner, "_DISCOVERY_PROGRESS_INTERVAL_SEC", 0.0):
            discovery = self._scanner._collect_candidates(
                roots=[str(scan_root.resolve())],
                progress=progress,
            )

        self.assertEqual(len(discovery), 12)
        self.assertTrue(any("Discovering files" in call[2] for call in progress_calls))
        self.assertTrue(any("12" in call[2] for call in progress_calls))

    def test_scan_reports_transition_after_discovery(self) -> None:
        scan_root = self._folder_a / "transition"
        scan_root.mkdir()
        (scan_root / "one.flac").write_bytes(b"")
        (scan_root / "two.flac").write_bytes(b"")

        progress_calls: list[tuple[int, int, str]] = []

        def progress(current: int, total: int, path: str) -> None:
            progress_calls.append((current, total, path))

        def fake_parse(path: Path, mtime_ns: int, size_bytes: int) -> _ParsedTrack:
            path_str = str(path.resolve())
            return _ParsedTrack(
                path=path_str,
                mtime_ns=mtime_ns,
                size_bytes=size_bytes,
                codec="flac",
                duration_sec=None,
                sample_rate=None,
                bit_depth=None,
                channels=None,
                title=path.stem,
                artist="Artist",
                album_artist="Artist",
                album="Album",
                release_id=ids.release_id("Artist", "Album"),
                is_synthetic=False,
                disc_number=None,
                track_number=None,
                year=None,
                genre=None,
                total_tracks=None,
                release_type_tag=None,
            )

        config = AppConfig(music_folders=[str(scan_root.resolve())])
        scanner = LibraryScanner(db_path=self._db_path, config=config)
        with (
            patch("tunes_player.core.library.scanner._parse_file", side_effect=fake_parse),
            patch("tunes_player.core.library.scanner.maintain_album_art", return_value=(0, 0)),
        ):
            scanner.scan(scan_folders=[str(scan_root.resolve())], progress=progress)

        transition = next(
            (call for call in progress_calls if call[0] > 0 and call[1] > 0 and call[2]),
            None,
        )
        self.assertIsNotNone(transition)
        assert transition is not None
        self.assertIn(".flac", transition[2])
        self.assertEqual(transition[0], 1)
        self.assertEqual(transition[1], 1)
        self.assertEqual(
            next((call for call in progress_calls if call[0] == 2 and call[1] == 2), None),
            (2, 2, ""),
        )

    def test_scan_progress_uses_expected_total(self) -> None:
        scan_root = self._folder_a / "progress_total"
        scan_root.mkdir()
        (scan_root / "one.flac").write_bytes(b"")
        (scan_root / "two.flac").write_bytes(b"")

        progress_calls: list[tuple[int, int, str]] = []

        def progress(current: int, total: int, path: str) -> None:
            progress_calls.append((current, total, path))

        config = AppConfig(music_folders=[str(scan_root.resolve())])
        scanner = LibraryScanner(db_path=self._db_path, config=config)
        with (
            patch("tunes_player.core.library.scanner._parse_file") as parse_file,
            patch("tunes_player.core.library.scanner.maintain_album_art", return_value=(0, 0)),
        ):
            parse_file.side_effect = AssertionError("unexpected parse during skip")
            scanner.scan(
                scan_folders=[str(scan_root.resolve())],
                progress=progress,
                expected_total=10_000,
            )

        scanning = [call for call in progress_calls if call[0] > 0 and call[1] > 0]
        self.assertTrue(scanning)
        self.assertEqual(scanning[0][:2], (1, 10_000))
        self.assertEqual(scanning[-1], (2, 10_000, ""))

    def test_lookup_matches_absolute_path_without_resolve(self) -> None:
        track = self._folder_a / "indexed.flac"
        track.write_bytes(b"data")
        path_str = str(track.resolve())
        stat = track.stat()
        connection = connect(self._db_path)
        try:
            connection.execute(
                "INSERT INTO files(path, mtime_ns, size_bytes, indexed_at_ns) VALUES (?, ?, ?, ?)",
                (path_str, stat.st_mtime_ns, stat.st_size, time.time_ns()),
            )
            connection.commit()
            existing = LibraryScanner._load_existing_files_metadata(
                connection,
                [str(self._folder_a.resolve())],
            )
            resolve_calls: list[str] = []
            original_resolve = Path.resolve

            def tracking_resolve(self, *args, **kwargs):
                resolve_calls.append(str(self))
                return original_resolve(self, *args, **kwargs)

            with patch.object(Path, "resolve", tracking_resolve):
                with patch("tunes_player.core.library.scanner._parse_file") as parse_file:
                    outcome, result_path, wrote, _art_added = self._scanner._process_candidate(
                        connection,
                        track,
                        file_errors=[],
                        existing_files=existing,
                    )
        finally:
            connection.close()

        parse_file.assert_not_called()
        self.assertEqual(outcome, "skipped")
        self.assertEqual(result_path, path_str)
        self.assertFalse(wrote)
        self.assertEqual(resolve_calls, [])

    def test_rescan_skips_symlinked_path_without_stat(self) -> None:
        scan_root = self._folder_a / "symlink_case"
        real_dir = scan_root / "real"
        real_dir.mkdir(parents=True)
        track = real_dir / "track.flac"
        track.write_bytes(b"data")
        link_dir = scan_root / "link"
        link_dir.symlink_to(real_dir, target_is_directory=True)
        walk_path = link_dir / "track.flac"
        db_path_str = str(track.resolve())

        connection = connect(self._db_path)
        try:
            connection.execute(
                "INSERT INTO files(path, mtime_ns, size_bytes, indexed_at_ns) VALUES (?, ?, ?, ?)",
                (db_path_str, 1, 1, time.time_ns()),
            )
            connection.commit()
            existing = LibraryScanner._load_existing_files_metadata(
                connection,
                [str(scan_root.resolve())],
            )
            with patch.object(Path, "stat") as stat_mock:
                outcome, result_path, wrote, _art_added = self._scanner._process_candidate(
                    connection,
                    walk_path,
                    file_errors=[],
                    existing_files=existing,
                )
        finally:
            connection.close()

        stat_mock.assert_not_called()
        self.assertEqual(outcome, "skipped")
        self.assertEqual(result_path, db_path_str)
        self.assertFalse(wrote)

    def test_load_existing_files_metadata_avoids_realpath(self) -> None:
        track = self._folder_a / "indexed.flac"
        track.write_bytes(b"data")
        path_str = str(track.resolve())
        root = str(self._folder_a.resolve())
        stat = track.stat()
        connection = connect(self._db_path)
        try:
            connection.execute(
                "INSERT INTO files(path, mtime_ns, size_bytes, indexed_at_ns) VALUES (?, ?, ?, ?)",
                (path_str, stat.st_mtime_ns, stat.st_size, time.time_ns()),
            )
            connection.commit()
            with patch("tunes_player.core.library.scanner.os.path.realpath") as realpath:
                existing = LibraryScanner._load_existing_files_metadata(
                    connection,
                    [root],
                )
        finally:
            connection.close()

        realpath.assert_not_called()
        self.assertIsNotNone(existing.get(path_str))

    def test_existing_file_index_normcase_lookup(self) -> None:
        canonical = str((self._folder_a / "track.flac").resolve())
        connection = connect(self._db_path)
        try:
            connection.execute(
                "INSERT INTO files(path, mtime_ns, size_bytes, indexed_at_ns) VALUES (?, ?, ?, ?)",
                (canonical, 1, 1, 1),
            )
            connection.commit()
            row = connection.execute(
                """
                SELECT id, path, mtime_ns, size_bytes, indexed_at_ns
                FROM files WHERE path = ?
                """,
                (canonical,),
            ).fetchone()
        finally:
            connection.close()

        index = ExistingFileIndex(
            by_path={canonical: row},
            by_normcase={"shared-normcase-key": canonical},
            by_normpath={},
        )
        with patch(
            "tunes_player.core.library.scanner.os.path.normcase",
            return_value="shared-normcase-key",
        ):
            hit = index.get("/walk/path/track.flac")

        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit[0], canonical)
        self.assertEqual(int(hit[1]["id"]), int(row["id"]))

    def test_lookup_normcase_index_matches_case_variant(self) -> None:
        canonical = str((self._folder_a / "Track.flac").resolve())
        walk_variant = str(self._folder_a / "track.flac")
        if os.path.normcase(canonical) != os.path.normcase(walk_variant):
            self.skipTest("platform normcase does not fold path case")

        (self._folder_a / "Track.flac").write_bytes(b"")
        connection = connect(self._db_path)
        try:
            connection.execute(
                "INSERT INTO files(path, mtime_ns, size_bytes, indexed_at_ns) VALUES (?, ?, ?, ?)",
                (canonical, 1, 1, time.time_ns()),
            )
            connection.commit()
            existing = LibraryScanner._load_existing_files_metadata(
                connection,
                [str(self._folder_a.resolve())],
            )
            path_str, row = self._scanner._lookup_existing_file(
                Path(walk_variant),
                existing,
                connection,
            )
        finally:
            connection.close()

        self.assertIsNotNone(row)
        self.assertEqual(path_str, canonical)

    def test_rescan_skips_m4a_probe_for_indexed_file(self) -> None:
        track = self._folder_a / "indexed.m4a"
        track.write_bytes(b"data")
        path_str = str(track.resolve())
        stat = track.stat()
        connection = connect(self._db_path)
        try:
            connection.execute(
                "INSERT INTO files(path, mtime_ns, size_bytes, indexed_at_ns) VALUES (?, ?, ?, ?)",
                (path_str, stat.st_mtime_ns, stat.st_size, time.time_ns()),
            )
            connection.commit()
            existing = LibraryScanner._load_existing_files_metadata(
                connection,
                [str(self._folder_a.resolve())],
            )
            with patch("tunes_player.core.library.scanner._parse_file") as parse_file:
                outcome, result_path, wrote, _art_added = self._scanner._process_candidate(
                    connection,
                    track,
                    file_errors=[],
                    existing_files=existing,
                )
        finally:
            connection.close()

        parse_file.assert_not_called()
        self.assertEqual(outcome, "skipped")
        self.assertEqual(result_path, path_str)
        self.assertFalse(wrote)

    def test_scan_backfills_art_when_files_unchanged(self) -> None:
        scan_root = self._folder_a / "unchanged"
        scan_root.mkdir()
        track = scan_root / "track.flac"
        track.write_bytes(b"x")
        path_str = str(track.resolve())
        stat = track.stat()
        album_id = ids.release_id("Artist", "Album")
        connection = connect(self._db_path)
        try:
            file_id = connection.execute(
                "INSERT INTO files(path, mtime_ns, size_bytes, indexed_at_ns) VALUES (?, ?, ?, ?)",
                (path_str, stat.st_mtime_ns, stat.st_size, time.time_ns()),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO tracks(
                    id, file_id, album_id, title, artist, album_artist, album, is_synthetic
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    ids.track_id(path_str),
                    file_id,
                    album_id,
                    "Track",
                    "Artist",
                    "Artist",
                    "Album",
                ),
            )
            connection.commit()
        finally:
            connection.close()

        config = AppConfig(music_folders=[str(scan_root.resolve())])
        scanner = LibraryScanner(db_path=self._db_path, config=config)
        with (
            patch(
                "tunes_player.core.library.art_cache.backfill_missing_album_art",
                return_value=1,
            ) as backfill,
            patch("tunes_player.core.library.scanner.maintain_album_art") as maintain,
        ):
            result = scanner.scan(scan_folders=[str(scan_root.resolve())])

        maintain.assert_not_called()
        backfill.assert_called_once()
        self.assertEqual(result.indexed, 0)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.removed, 0)
        self.assertEqual(result.art_indexed, 1)

    def test_iter_audio_candidates_skips_junk_directories(self) -> None:
        scan_root = self._folder_a / "prune"
        scan_root.mkdir()
        music_dir = scan_root / "Artist"
        music_dir.mkdir()
        (music_dir / "track.flac").write_bytes(b"")
        junk = scan_root / "@eaDir"
        junk.mkdir()
        (junk / "hidden.flac").write_bytes(b"")

        discovery = list(
            self._scanner._iter_audio_candidates(roots=[str(scan_root.resolve())]),
        )

        self.assertEqual(len(discovery), 1)
        self.assertEqual(discovery[0].name, "track.flac")

    def test_new_file_scan_indexes_without_prior_row(self) -> None:
        scan_root = self._folder_a / "fresh"
        scan_root.mkdir()
        track = scan_root / "new.flac"
        track.write_bytes(b"data")

        def fake_parse(path: Path, mtime_ns: int, size_bytes: int) -> _ParsedTrack:
            path_str = str(path.resolve())
            return _ParsedTrack(
                path=path_str,
                mtime_ns=mtime_ns,
                size_bytes=size_bytes,
                codec="flac",
                duration_sec=None,
                sample_rate=None,
                bit_depth=None,
                channels=None,
                title=path.stem,
                artist="Artist",
                album_artist="Artist",
                album="Album",
                release_id=ids.release_id("Artist", "Album"),
                is_synthetic=False,
                disc_number=None,
                track_number=None,
                year=None,
                genre=None,
                total_tracks=None,
                release_type_tag=None,
            )

        config = AppConfig(music_folders=[str(scan_root.resolve())])
        scanner = LibraryScanner(db_path=self._db_path, config=config)
        with (
            patch("tunes_player.core.library.scanner._parse_file", side_effect=fake_parse),
            patch("tunes_player.core.library.scanner.maintain_album_art", return_value=(0, 0)),
        ):
            result = scanner.scan(scan_folders=[str(scan_root.resolve())])

        connection = connect(self._db_path)
        try:
            count = connection.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"]
        finally:
            connection.close()

        self.assertEqual(result.indexed, 1)
        self.assertEqual(count, 1)

    def test_parse_m4a_opens_mp4_once(self) -> None:
        track = self._folder_a / "song.m4a"
        track.write_bytes(b"not-a-real-m4a")

        with patch("mutagen.mp4.MP4") as mp4_cls:
            instance = mp4_cls.return_value
            instance.info.codec = "mp4a"
            instance.tags = {"\xa9nam": ["Song"]}
            parsed = _parse_file(track, 1, 1)
            self.assertEqual(mp4_cls.call_count, 1)
            self.assertEqual(parsed.codec, "aac")

        with patch("mutagen.mp4.MP4") as mp4_cls:
            instance = mp4_cls.return_value
            instance.info.codec = "unknown"
            instance.tags = {}
            with self.assertRaises(_UnsupportedTier1Path):
                _parse_file(track, 1, 1)

    def test_commit_scan_batch_commits_and_notifies(self) -> None:
        connection = connect(self._db_path)
        notifications: list[tuple[int, int]] = []
        try:
            connection.execute("BEGIN")
            last = LibraryScanner._commit_scan_batch(
                connection,
                batch_notify=lambda indexed, art_indexed: notifications.append(
                    (indexed, art_indexed),
                ),
                indexed=2,
                art_indexed=5,
                last_notified=(0, 0),
            )
        finally:
            connection.close()

        self.assertEqual(last, (2, 5))
        self.assertEqual(notifications, [(2, 5)])


if __name__ == "__main__":
    unittest.main()
