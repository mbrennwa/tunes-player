"""Tests for labels sync folder path heuristics (#97)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tunes_player.core.labels_sync.path_hints import (
    looks_like_known_sync_folder,
    unrecognized_sync_folder_advisory,
)


class LabelsSyncPathHintsTests(unittest.TestCase):
    def test_empty_path_unrecognized(self) -> None:
        self.assertFalse(looks_like_known_sync_folder(""))
        self.assertFalse(looks_like_known_sync_folder("   "))

    def test_path_markers_recognized(self) -> None:
        cases = (
            "/home/u/Nextcloud/TunesLabels",
            "/home/u/ownCloud/labels",
            "/Users/u/Dropbox/tunes",
            "/home/u/Google Drive/Tunes",
            "/home/u/GoogleDrive/Tunes",
            "/home/u/OneDrive/Music",
            "/home/u/iCloud Drive/Tunes",
            "/home/u/Syncthing/shared",
            "/mnt/Resilio/sync",
            "/home/u/MEGA/Tunes",
            "/home/u/pCloudDrive/labels",
            "/home/u/Seafile/library",
            "/mnt/rclone/gdrive/tunes",
        )
        for path in cases:
            with self.subTest(path=path):
                self.assertTrue(looks_like_known_sync_folder(path))

    def test_plain_local_path_unrecognized(self) -> None:
        self.assertFalse(
            looks_like_known_sync_folder("/tmp/tunes-labels-only-local"),
        )
        self.assertFalse(
            looks_like_known_sync_folder(
                str(Path.home() / "Documents" / "tunes-labels"),
            ),
        )

    def test_stfolder_marker_recognized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".stfolder").mkdir()
            child = root / "labels"
            child.mkdir()
            self.assertTrue(looks_like_known_sync_folder(root))
            self.assertTrue(looks_like_known_sync_folder(child))

    def test_dropbox_marker_on_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".dropbox").mkdir()
            nested = root / "a" / "b"
            nested.mkdir(parents=True)
            self.assertTrue(looks_like_known_sync_folder(nested))

    def test_csync_journal_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".csync_journal.db").write_bytes(b"")
            self.assertTrue(looks_like_known_sync_folder(root))

    def test_advisory_copy(self) -> None:
        text = unrecognized_sync_folder_advisory()
        self.assertIn("recognized sync folder", text)
        self.assertIn("another tool", text)


if __name__ == "__main__":
    unittest.main()
