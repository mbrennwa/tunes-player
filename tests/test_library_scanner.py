"""Library scanner scoped folder tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tunes_player.core.config import AppConfig
from tunes_player.core.library import ids
from tunes_player.core.library.db import connect
from tunes_player.core.library.scanner import LibraryScanner, _ParsedTrack


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
            patch("tunes_player.core.library.scanner.backfill_missing_album_art", return_value=0),
            patch("tunes_player.core.library.scanner.prune_orphan_album_art"),
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
            patch("tunes_player.core.library.scanner.backfill_missing_album_art", return_value=0),
            patch("tunes_player.core.library.scanner.prune_orphan_album_art"),
        ):
            result = self._scanner.scan(scan_folders=[str(scan_root.resolve())])

        self.assertEqual(result.errors, 1)
        self.assertEqual(result.indexed, 1)
        self.assertEqual(len(result.file_errors), 1)
        self.assertIn("broken.flac", result.file_errors[0].path)
        self.assertEqual(result.file_errors[0].reason, "invalid FLAC header")

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
            patch("tunes_player.core.library.scanner.backfill_missing_album_art", return_value=0),
            patch("tunes_player.core.library.scanner.prune_orphan_album_art"),
        ):
            result = self._scanner.scan(scan_folders=[str(scan_root.resolve())])

        self.assertEqual(result.errors, 1)
        self.assertEqual(result.indexed, 1)
        self.assertEqual(len(result.file_errors), 1)
        self.assertIn("bad.flac", result.file_errors[0].path)
        self.assertEqual(result.file_errors[0].reason, "database write failed")


if __name__ == "__main__":
    unittest.main()
