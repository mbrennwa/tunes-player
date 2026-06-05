"""Tests for filesystem-driven library scan scheduling."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gi.repository import Gio, GLib

from tunes_player.core.config import ConfigManager
from tunes_player.core.services import PlayerService
from tunes_player.ui.gtk.folder_monitor import FolderMonitorManager, _DEBOUNCE_MS


class FolderMonitorManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._config = ConfigManager(Path(self._tmpdir.name) / "config.json")
        self._config.load()
        self._service = PlayerService(config=self._config)
        self._folder = str(Path(self._tmpdir.name) / "music")
        Path(self._folder).mkdir()
        self._manager = FolderMonitorManager(self._service)

    def tearDown(self) -> None:
        self._manager.stop()
        self._tmpdir.cleanup()

    def test_start_enqueues_startup_scan_only(self) -> None:
        self._config.add_music_folder(self._folder, auto_monitor=True)
        with (
            patch.object(self._service, "enqueue_startup_art_maintenance") as art,
            patch.object(self._service, "enqueue_startup_scans") as startup,
            patch.object(GLib, "timeout_add_seconds") as periodic,
            patch.object(self._manager, "_sync_monitors"),
        ):
            self._manager.start()

        startup.assert_called_once_with()
        art.assert_called_once_with()
        periodic.assert_not_called()

    def test_startup_art_maintenance_defers_while_scans_pending(self) -> None:
        self._config.add_music_folder(self._folder, auto_monitor=True)
        with patch("threading.Thread") as thread:
            self._service.enqueue_scan(folder=self._folder)
            self._service.enqueue_startup_art_maintenance()

        thread.assert_not_called()
        self.assertTrue(self._service._pending_startup_art_maintenance)

    def test_filesystem_change_schedules_debounced_incremental_scan(self) -> None:
        self._config.add_music_folder(self._folder, auto_monitor=True)
        with patch.object(GLib, "timeout_add", return_value=42) as timeout_add:
            self._manager._record_add(self._folder, str(Path(self._folder) / "song.flac"))
            self._manager._schedule_debounced_incremental_scan(self._folder)

        timeout_add.assert_called_once()
        delay_ms, callback, folder = timeout_add.call_args[0]
        self.assertEqual(delay_ms, _DEBOUNCE_MS)
        self.assertEqual(folder, self._folder)
        self.assertEqual(callback.__func__, self._manager._run_debounced_incremental_scan.__func__)

    def test_debounced_incremental_scan_enqueues_changed_paths(self) -> None:
        self._config.add_music_folder(self._folder, auto_monitor=True)
        added = str((Path(self._folder) / "new.flac").resolve())
        removed = str((Path(self._folder) / "gone.flac").resolve())
        self._manager._pending_adds[self._folder] = {added}
        self._manager._pending_removes[self._folder] = {removed}
        with patch.object(self._service, "enqueue_incremental_scan") as enqueue:
            self._manager._run_debounced_incremental_scan(self._folder)

        enqueue.assert_called_once_with(
            folder=self._folder,
            add_paths=[added],
            remove_paths=[removed],
        )

    def test_debounced_incremental_scan_skips_when_auto_monitor_disabled(self) -> None:
        self._config.add_music_folder(self._folder, auto_monitor=False)
        self._manager._pending_adds[self._folder] = {"x"}
        with patch.object(self._service, "enqueue_incremental_scan") as enqueue:
            self._manager._run_debounced_incremental_scan(self._folder)

        enqueue.assert_not_called()

    def test_record_add_and_remove_cancel_each_other(self) -> None:
        path = str((Path(self._folder) / "track.flac").resolve())
        self._manager._record_add(self._folder, path)
        self._manager._record_remove(self._folder, path)
        self.assertEqual(self._manager._pending_adds.get(self._folder, set()), set())
        self.assertEqual(self._manager._pending_removes.get(self._folder, set()), set())

    def test_unknown_path_falls_back_to_full_scan(self) -> None:
        self._config.add_music_folder(self._folder, auto_monitor=True)
        with (
            patch.object(GLib, "timeout_add", return_value=7) as timeout_add,
            patch.object(self._manager, "_path_from_file", return_value=None),
        ):
            self._manager._on_changed(
                None,
                Gio.File.new_for_path(self._folder),
                None,
                Gio.FileMonitorEvent.CREATED,
                self._folder,
            )

        timeout_add.assert_called_once()
        self.assertEqual(
            timeout_add.call_args[0][1].__func__,
            self._manager._run_debounced_full_scan.__func__,
        )


if __name__ == "__main__":
    unittest.main()
