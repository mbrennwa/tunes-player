"""Tests for per-folder scan status formatting and persistence."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tunes_player.core.config import ConfigManager
from tunes_player.core.folder_scan_status import (
    FOLDER_SCAN_INCOMPLETE,
    format_folder_last_scan_line,
)


class FolderScanStatusFormatTests(unittest.TestCase):
    def test_never_scanned(self) -> None:
        self.assertEqual(
            format_folder_last_scan_line(scanned_at=None, errors=None),
            "Last scan: never",
        )

    def test_successful_scan_with_errors(self) -> None:
        line = format_folder_last_scan_line(scanned_at=1_700_000_000.0, errors=3)
        self.assertIn("Last scan:", line)
        self.assertIn("3 errors", line)

    def test_failed_scan(self) -> None:
        line = format_folder_last_scan_line(scanned_at=1_700_000_000.0, errors=-1)
        self.assertIn("scan failed", line)

    def test_incomplete_scan(self) -> None:
        line = format_folder_last_scan_line(
            scanned_at=1_700_000_000.0,
            errors=FOLDER_SCAN_INCOMPLETE,
        )
        self.assertIn("incomplete", line)
        self.assertNotIn("scan failed", line)


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
        self._config.record_folder_scan(self._folder, errors=2, scanned_at=1_700_000_100.0)
        self._config.load()
        self.assertEqual(self._config.folder_last_scan_at(self._folder), 1_700_000_100.0)
        self.assertEqual(self._config.folder_last_scan_errors(self._folder), 2)

    def test_remove_folder_clears_scan_status(self) -> None:
        self._config.record_folder_scan(self._folder, errors=0)
        self._config.remove_music_folder(self._folder)
        raw = json.loads(self._config.path.read_text(encoding="utf-8"))
        self.assertEqual(raw.get("music_folder_last_scan_at", {}), {})
        self.assertEqual(raw.get("music_folder_last_scan_errors", {}), {})


if __name__ == "__main__":
    unittest.main()
