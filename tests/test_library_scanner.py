"""Library scanner scoped folder tests."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tunes_player.core.config import AppConfig
from tunes_player.core.library import ids
from tunes_player.core.library.db import connect
from tunes_player.core.library.scanner import LibraryScanner, ScanResult, _ParsedTrack


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
        candidates = self._scanner._collect_candidates(roots=roots)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].name, "track_a.flac")
        self.assertTrue(str(candidates[0]).startswith(str(self._folder_a.resolve())))

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
            patch("tunes_player.core.library.scanner.index_album_art_for_file"),
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
            patch("tunes_player.core.library.scanner.index_album_art_for_file"),
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
            patch("tunes_player.core.library.scanner.maintain_album_art", return_value=(0, 0)),
            patch(
                "tunes_player.core.library.art_cache.extract_embedded_art",
                return_value=(b"jpeg-bytes", "image/jpeg"),
            ),
        ):
            result = self._scanner.scan(scan_folders=[str(scan_root.resolve())])

        self.assertEqual(result.errors, 0)
        self.assertEqual(result.indexed, 1)

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
            patch("tunes_player.core.library.scanner.index_album_art_for_file"),
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
                return_value=("skipped", None),
            ),
            patch(
                "tunes_player.core.library.scanner.maintain_album_art",
                return_value=(0, 0),
            ),
        ):
            self._scanner.scan(scan_folders=[str(self._folder_a.resolve())])

        self.assertGreaterEqual(len(commits), 3)

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
            patch("tunes_player.core.library.scanner.index_album_art_for_file"),
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


if __name__ == "__main__":
    unittest.main()
