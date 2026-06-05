"""Tests for per-folder automatic scan/monitor settings."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tunes_player.core.config import ConfigManager
from tunes_player.core.folder_scan_status import FOLDER_SCAN_INCOMPLETE
from tunes_player.core.services import PlayerService


class FolderAutoMonitorConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._config_path = Path(self._tmpdir.name) / "config.json"
        self._config = ConfigManager(self._config_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_add_folder_persists_auto_monitor_flag(self) -> None:
        folder = str(Path(self._tmpdir.name) / "music")
        Path(folder).mkdir()
        self._config.add_music_folder(folder, auto_monitor=True)
        self._config.load()
        self.assertTrue(self._config.folder_auto_monitor_enabled(folder))

    def test_remove_folder_clears_auto_monitor_flag(self) -> None:
        folder = str(Path(self._tmpdir.name) / "music")
        Path(folder).mkdir()
        self._config.add_music_folder(folder, auto_monitor=True)
        self._config.remove_music_folder(folder)
        self._config.load()
        self.assertFalse(self._config.folder_auto_monitor_enabled(folder))

    def test_set_folder_auto_monitor_accepts_path_alias(self) -> None:
        real = Path(self._tmpdir.name) / "music"
        real.mkdir()
        link = Path(self._tmpdir.name) / "link"
        link.symlink_to(real, target_is_directory=True)
        self._config.add_music_folder(str(real), auto_monitor=True)

        self._config.set_folder_auto_monitor(str(link), enabled=False)

        self.assertFalse(self._config.folder_auto_monitor_enabled(str(real)))

    def test_config_roundtrip_omits_disabled_monitor_entries(self) -> None:
        folder = str(Path(self._tmpdir.name) / "music")
        Path(folder).mkdir()
        self._config.add_music_folder(folder, auto_monitor=False)
        raw = json.loads(self._config_path.read_text(encoding="utf-8"))
        self.assertNotIn(folder, raw.get("music_folder_auto_monitor", {}))


class FolderAutoMonitorServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._config = ConfigManager(Path(self._tmpdir.name) / "config.json")
        self._config.load()
        self._service = PlayerService(config=self._config)
        self._folder = str(Path(self._tmpdir.name) / "music")
        Path(self._folder).mkdir()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_add_music_folder_with_auto_monitor_enqueues_scan(self) -> None:
        with patch.object(self._service, "enqueue_scan") as enqueue:
            self._service.add_music_folder(self._folder, auto_monitor=True)
        enqueue.assert_called_once_with(folder=self._folder, priority=True)

    def test_enqueue_scan_queues_multiple_folders(self) -> None:
        other = str(Path(self._tmpdir.name) / "other")
        Path(other).mkdir()
        self._config.add_music_folder(self._folder)
        self._config.add_music_folder(other)

        with patch.object(self._service, "_start_scan") as start_scan:
            self._service.enqueue_scan(folder=self._folder)
            self._service._scan_queue = object()
            self._service.enqueue_scan(folder=other)

        start_scan.assert_called_once_with(self._folder)
        self.assertEqual(self._service._pending_scan_folders, [other])

    def test_scan_library_promotes_folder_to_front_of_queue(self) -> None:
        other = str(Path(self._tmpdir.name) / "other")
        Path(other).mkdir()
        self._config.add_music_folder(self._folder)
        self._config.add_music_folder(other)
        self._service.enqueue_scan(folder=other)

        with patch.object(self._service, "_try_start_scan") as try_start:
            self._service.scan_library(folder=self._folder)

        self.assertEqual(self._service._pending_scan_folders[0], self._folder)
        try_start.assert_called_once_with()

    def test_remove_music_folder_stops_active_scan_before_purge(self) -> None:
        self._config.add_music_folder(self._folder)
        process = MagicMock()
        process.is_alive.return_value = True
        self._service._scan_process = process
        self._service._scan_queue = object()

        with patch.object(self._service, "notify_library_updated"):
            with patch.object(self._service._store, "close") as close_store:
                with patch.object(self._service._store, "reconnect") as reconnect_store:
                    with patch(
                        "tunes_player.core.services.LibraryScanner.purge_folder",
                        return_value=2,
                    ) as purge:
                        removed = self._service.remove_music_folder(self._folder)

        process.terminate.assert_called_once_with()
        process.join.assert_called_once()
        close_store.assert_called_once_with()
        purge.assert_called_once()
        reconnect_store.assert_called_once_with()
        self.assertEqual(removed, 2)
        self.assertFalse(self._config.config.music_folders)

    def test_enqueue_startup_scans_only_auto_monitor_folders(self) -> None:
        other = str(Path(self._tmpdir.name) / "other")
        Path(other).mkdir()
        self._config.add_music_folder(self._folder, auto_monitor=True)
        self._config.add_music_folder(other, auto_monitor=False)

        with patch.object(self._service, "enqueue_scan") as enqueue:
            self._service.enqueue_startup_scans()

        enqueue.assert_called_once_with(folder=self._folder)

    def test_disable_auto_monitor_stops_active_scan(self) -> None:
        self._config.add_music_folder(self._folder, auto_monitor=True)
        resolved = str(Path(self._folder).expanduser().resolve())
        process = MagicMock()
        process.is_alive.return_value = True
        self._service._scan_process = process
        self._service._scan_queue = object()
        self._service._scanning_folder = resolved

        with patch.object(self._service._config_manager, "record_folder_scan") as record:
            with patch.object(self._service, "notify_library_updated") as notify:
                with patch.object(self._service, "_emit") as emit:
                    self._service.set_folder_auto_monitor(self._folder, enabled=False)

        process.terminate.assert_called_once_with()
        self.assertIsNone(self._service._scanning_folder)
        record.assert_called_once_with(resolved, errors=FOLDER_SCAN_INCOMPLETE)
        notify.assert_called_once_with()
        emit.assert_any_call("scan_finished")

    def test_disable_auto_monitor_drops_queued_scan(self) -> None:
        other = str(Path(self._tmpdir.name) / "other")
        Path(other).mkdir()
        self._config.add_music_folder(self._folder, auto_monitor=True)
        self._config.add_music_folder(other, auto_monitor=True)
        resolved = str(Path(self._folder).expanduser().resolve())
        resolved_other = str(Path(other).expanduser().resolve())
        self._service._pending_scan_folders = [resolved, resolved_other]
        self._service._scan_queue = object()

        self._service.set_folder_auto_monitor(self._folder, enabled=False)

        self.assertEqual(self._service._pending_scan_folders, [resolved_other])


if __name__ == "__main__":
    unittest.main()
