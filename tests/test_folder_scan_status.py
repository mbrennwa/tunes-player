"""Tests for per-folder scan status formatting and persistence."""

from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path

from tunes_player.core.config import ConfigManager
from tunes_player.core.folder_scan_status import (
    DIAGNOSTICS_SCAN_HINT,
    FOLDER_SCAN_FAILED,
    FOLDER_SCAN_INCOMPLETE,
    format_folder_last_scan_line,
    log_folder_scan_failure,
)
from tunes_player.core.library.scanner import ScanFileError


class FolderScanStatusFormatTests(unittest.TestCase):
    def test_never_scanned(self) -> None:
        self.assertEqual(
            format_folder_last_scan_line(scanned_at=None, errors=None),
            "Last scan: never",
        )

    def test_never_scanned_with_partial_index_shows_coverage(self) -> None:
        line = format_folder_last_scan_line(
            scanned_at=None,
            errors=None,
            indexed_files=12_319,
            catalog_total=18_050,
        )
        self.assertIn("Last scan: never", line)
        self.assertIn("12,319 / 18,050 files indexed", line)
        self.assertIn("incomplete", line)

    def test_partial_index_after_incremental_scan(self) -> None:
        line = format_folder_last_scan_line(
            scanned_at=1_700_000_000.0,
            errors=0,
            indexed_files=12_319,
            catalog_total=18_050,
            last_scan_kind="incremental",
        )
        self.assertIn("12,319 / 18,050 files indexed", line)
        self.assertIn("incomplete", line)
        self.assertNotIn("no errors", line)
        self.assertNotIn(" · complete", line)

    def test_complete_after_full_scan(self) -> None:
        line = format_folder_last_scan_line(
            scanned_at=1_700_000_000.0,
            errors=0,
            indexed_files=18_050,
            catalog_total=18_050,
            last_scan_kind="full",
        )
        self.assertEqual(line, "Last scan: 2023-11-14 23:13 · 18,050 files")
        self.assertNotIn(DIAGNOSTICS_SCAN_HINT, line)

    def test_successful_scan_with_errors_refers_to_diagnostics(self) -> None:
        line = format_folder_last_scan_line(
            scanned_at=1_700_000_000.0,
            errors=3,
            indexed_files=17_900,
            catalog_total=18_050,
            last_scan_kind="full",
        )
        self.assertIn("Last scan:", line)
        self.assertIn("3 errors", line)
        self.assertIn(DIAGNOSTICS_SCAN_HINT, line)
        self.assertNotIn(" · complete", line)

    def test_failed_scan_refers_to_diagnostics(self) -> None:
        line = format_folder_last_scan_line(
            scanned_at=1_700_000_000.0,
            errors=FOLDER_SCAN_FAILED,
            indexed_files=12_319,
            catalog_total=18_050,
            last_scan_kind="full",
        )
        self.assertIn("scan failed", line)
        self.assertIn(DIAGNOSTICS_SCAN_HINT, line)

    def test_incomplete_scan_does_not_refer_to_diagnostics(self) -> None:
        line = format_folder_last_scan_line(
            scanned_at=1_700_000_000.0,
            errors=FOLDER_SCAN_INCOMPLETE,
            indexed_files=12_319,
            catalog_total=18_050,
            last_scan_kind="full",
        )
        self.assertIn("incomplete", line)
        self.assertNotIn(DIAGNOSTICS_SCAN_HINT, line)


class FolderScanStatusLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._records: list[str] = []
        self._handler = logging.Handler()
        self._handler.emit = lambda record: self._records.append(record.getMessage())
        self._logger = logging.getLogger("tunes_player.scan")
        self._logger.addHandler(self._handler)
        self._logger.setLevel(logging.ERROR)

    def tearDown(self) -> None:
        self._logger.removeHandler(self._handler)

    def test_logs_fatal_scan_failure(self) -> None:
        log_path = Path("/tmp/tunes-player.log")
        log_folder_scan_failure(
            "/music",
            errors=FOLDER_SCAN_FAILED,
            log_path=log_path,
            fatal_error="database locked",
        )
        joined = "\n".join(self._records)
        self.assertIn("/music", joined)
        self.assertIn("database locked", joined)
        self.assertIn(str(log_path), joined)

    def test_logs_per_file_errors(self) -> None:
        log_path = Path("/tmp/tunes-player.log")
        log_folder_scan_failure(
            "/music",
            errors=2,
            log_path=log_path,
            file_errors=(
                ScanFileError("/music/a.flac", "invalid FLAC"),
                ScanFileError("/music/b.flac", "permission denied"),
            ),
        )
        joined = "\n".join(self._records)
        self.assertIn("2 file error(s)", joined)
        self.assertIn("/music/a.flac: invalid FLAC", joined)
        self.assertIn("/music/b.flac: permission denied", joined)
        self.assertIn(str(log_path), joined)


class FolderScanStatusConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._config = ConfigManager(Path(self._tmpdir.name) / "config.json")
        self._folder = str(Path(self._tmpdir.name) / "music")
        Path(self._folder).mkdir()
        self._config.add_music_folder(self._folder)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_record_folder_scan_persists(self) -> None:
        self._config.record_folder_scan(
            self._folder,
            errors=2,
            scanned_at=1_700_000_100.0,
            scan_kind="full",
            catalog_total=18_050,
        )
        self._config.load()
        self.assertEqual(self._config.folder_last_scan_at(self._folder), 1_700_000_100.0)
        self.assertEqual(self._config.folder_last_scan_errors(self._folder), 2)
        self.assertEqual(self._config.folder_catalog_total(self._folder), 18_050)
        self.assertEqual(self._config.folder_last_scan_kind(self._folder), "full")

    def test_incremental_scan_does_not_update_catalog_total(self) -> None:
        self._config.record_folder_scan(
            self._folder,
            errors=0,
            scan_kind="full",
            catalog_total=18_050,
        )
        self._config.record_folder_scan(
            self._folder,
            errors=0,
            scan_kind="incremental",
            catalog_total=99,
        )
        self._config.load()
        self.assertEqual(self._config.folder_catalog_total(self._folder), 18_050)
        self.assertEqual(self._config.folder_last_scan_kind(self._folder), "incremental")

    def test_remove_folder_clears_scan_status(self) -> None:
        self._config.record_folder_scan(
            self._folder,
            errors=0,
            scan_kind="full",
            catalog_total=100,
        )
        self._config.set_folder_scan_checkpoint(
            self._folder,
            str(Path(self._folder) / "track.flac"),
        )
        self._config.remove_music_folder(self._folder)
        raw = json.loads(self._config.path.read_text(encoding="utf-8"))
        self.assertEqual(raw.get("music_folder_last_scan_at", {}), {})
        self.assertEqual(raw.get("music_folder_last_scan_errors", {}), {})
        self.assertEqual(raw.get("music_folder_catalog_total", {}), {})
        self.assertEqual(raw.get("music_folder_last_scan_kind", {}), {})
        self.assertEqual(raw.get("music_folder_scan_checkpoint", {}), {})

    def test_scan_checkpoint_persists_and_clears_on_success(self) -> None:
        checkpoint = str(Path(self._folder) / "album" / "track.flac")
        self._config.record_folder_scan(
            self._folder,
            errors=FOLDER_SCAN_INCOMPLETE,
            scan_kind="full",
            catalog_total=100,
            checkpoint=checkpoint,
        )
        self._config.load()
        self.assertEqual(self._config.folder_scan_checkpoint(self._folder), checkpoint)

        self._config.record_folder_scan(
            self._folder,
            errors=0,
            scan_kind="full",
            catalog_total=100,
        )
        self._config.load()
        self.assertIsNone(self._config.folder_scan_checkpoint(self._folder))

    def test_set_folder_scan_checkpoint_roundtrip(self) -> None:
        checkpoint = str(Path(self._folder) / "music.flac")
        self._config.set_folder_scan_checkpoint(self._folder, checkpoint)
        self._config.load()
        self.assertEqual(self._config.folder_scan_checkpoint(self._folder), checkpoint)
        self._config.set_folder_scan_checkpoint(self._folder, None)
        self._config.load()
        self.assertIsNone(self._config.folder_scan_checkpoint(self._folder))


if __name__ == "__main__":
    unittest.main()
