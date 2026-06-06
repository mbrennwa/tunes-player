"""Tests for per-folder automatic scan/monitor settings."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tunes_player.core.config import ConfigManager
from tunes_player.core.folder_scan_status import FOLDER_SCAN_INCOMPLETE
from tunes_player.core.models import Source, Track
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

        with patch.object(self._service, "_start_scan_job") as start_scan:
            self._service.enqueue_scan(folder=self._folder)
            self._service._scan_queue = object()
            self._service.enqueue_scan(folder=other)

        start_scan.assert_called_once()
        self.assertEqual(start_scan.call_args[0][0].folder, str(Path(self._folder).resolve()))
        self.assertEqual([job.folder for job in self._service._pending_scan_jobs], [other])

    def test_scan_library_promotes_folder_to_front_of_queue(self) -> None:
        other = str(Path(self._tmpdir.name) / "other")
        Path(other).mkdir()
        self._config.add_music_folder(self._folder)
        self._config.add_music_folder(other)
        self._service.enqueue_scan(folder=other)

        with patch.object(self._service, "_try_start_scan") as try_start:
            self._service.scan_library(folder=self._folder)

        self.assertEqual(self._service._pending_scan_jobs[0].folder, self._folder)
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

        with (
            patch.object(self._service, "_run_startup_reconcile") as reconcile,
            patch.object(self._service, "enqueue_scan") as enqueue,
        ):
            self._service.enqueue_startup_scans()

        reconcile.assert_called_once_with()
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
        from tunes_player.core.services import _ScanJob

        self._service._pending_scan_jobs = [
            _ScanJob(folder=resolved),
            _ScanJob(folder=resolved_other),
        ]
        self._service._scan_queue = object()

        self._service.set_folder_auto_monitor(self._folder, enabled=False)

        self.assertEqual(
            [job.folder for job in self._service._pending_scan_jobs],
            [resolved_other],
        )

    def test_enqueue_incremental_scan_merges_pending_jobs_for_same_folder(self) -> None:
        self._config.add_music_folder(self._folder)
        first = str((Path(self._folder) / "a.flac").resolve())
        second = str((Path(self._folder) / "b.flac").resolve())
        stale = str((Path(self._folder) / "gone.flac").resolve())
        from tunes_player.core.services import _ScanJob

        self._service._pending_scan_jobs = [
            _ScanJob(folder=str(Path(self._folder).resolve()), add_paths=(first,), remove_paths=()),
        ]
        self._service._scan_queue = object()
        with patch.object(self._service, "_start_scan_job"):
            self._service.enqueue_incremental_scan(
                folder=self._folder,
                add_paths=[second],
                remove_paths=[stale],
            )

        self.assertEqual(len(self._service._pending_scan_jobs), 1)
        job = self._service._pending_scan_jobs[0]
        self.assertEqual(set(job.add_paths), {first, second})
        self.assertEqual(set(job.remove_paths), {stale})

    def test_start_scan_closes_store_write_connection(self) -> None:
        process = MagicMock()
        with patch(
            "tunes_player.core.services.create_scan_process",
            return_value=(process, MagicMock()),
        ):
            from tunes_player.core.services import _ScanJob

            self._service._start_scan_job(_ScanJob(folder=str(Path(self._folder).resolve())))

        self.assertIsNone(self._service._store._write_connection)
        process.start.assert_called_once()

    def test_store_reads_work_while_write_connection_closed(self) -> None:
        self._service._store.close()
        self.assertIsNone(self._service._store._write_connection)
        self.assertIsInstance(self._service._store.track_count(), int)

    def test_record_playback_deferred_while_scanning(self) -> None:
        track = Track(
            id="local:file:one",
            title="Track",
            artist_name="Artist",
            album_title="Album",
            source=Source.LOCAL,
        )
        self._service._scan_queue = object()
        with (
            patch.object(self._service, "_release_id_for_playback", return_value="local:release:1"),
            patch.object(self._service._store, "record_play") as record_play,
        ):
            self._service._record_playback(track)
            record_play.assert_not_called()
            self.assertEqual(len(self._service._deferred_plays), 1)
            self._service._flush_deferred_plays()
            record_play.assert_called_once()


if __name__ == "__main__":
    unittest.main()
