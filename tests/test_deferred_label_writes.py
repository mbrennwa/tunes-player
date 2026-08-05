"""Defer label writes while the library store write connection is closed (#74)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tunes_player.core.config import ConfigManager
from tunes_player.core.models import Release, ReleaseType, Source
from tunes_player.core.services import PlayerService


def _local_release(release_id: str) -> Release:
    return Release(
        id=release_id,
        title="Album",
        artist_name="Artist",
        source=Source.LOCAL,
        track_count=1,
        release_type=ReleaseType.ALBUM,
    )


class DeferredLabelWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._data_dir = root / "data"
        self._data_dir.mkdir()
        self._patcher = patch(
            "tunes_player.core.config.user_data_dir",
            return_value=str(self._data_dir),
        )
        self._patcher.start()
        self._config = ConfigManager(root / "config.json")
        self._config.load()
        self._service = PlayerService(config=self._config)

    def tearDown(self) -> None:
        self._service.shutdown()
        self._patcher.stop()
        self._tmp.cleanup()

    def test_list_user_labels_works_while_write_closed(self) -> None:
        self._service.toggle_release_label("tidal:album:1", "buy", on=True)
        self._service._store.close()
        self.assertFalse(self._service._store.writes_available())
        self.assertEqual(self._service.list_user_labels(), ("buy",))

    def test_toggle_defers_while_write_closed_and_flushes_on_reconnect(self) -> None:
        self._service._store.close()
        self.assertFalse(self._service._store.writes_available())
        self._service.toggle_release_label("tidal:album:2", "vinyl", on=True)
        self.assertEqual(
            self._service.get_release_labels("tidal:album:2"),
            frozenset({"vinyl"}),
        )
        self.assertIn("vinyl", self._service.list_user_labels())
        # Not persisted yet — store has no write connection.
        self.assertEqual(
            self._service._store.get_release_label_names("tidal:album:2"),
            frozenset(),
        )
        self._service._store.reconnect()
        self._service._flush_deferred_label_ops()
        self.assertEqual(
            self._service._store.get_release_label_names("tidal:album:2"),
            frozenset({"vinyl"}),
        )
        self.assertTrue(self._service._store.has_dirty_label_sync_rows())

    def test_toggle_coalesce_last_wins(self) -> None:
        self._service._store.close()
        self._service.toggle_release_label("tidal:album:3", "keep", on=True)
        self._service.toggle_release_label("tidal:album:3", "keep", on=False)
        self._service._store.reconnect()
        self._service._flush_deferred_label_ops()
        self.assertEqual(
            self._service._store.get_release_label_names("tidal:album:3"),
            frozenset(),
        )

    def test_list_labelled_includes_deferred_toggles(self) -> None:
        release = _local_release("local:album:deferred")
        self._service.get_release_summary = MagicMock(return_value=release)
        self._service._store.close()
        self._service.toggle_release_label(release.id, "Like!", on=True)
        labelled = self._service.list_labelled_releases()
        self.assertEqual([item.id for item in labelled], [release.id])

    def test_shutdown_flushes_deferred_labels(self) -> None:
        self._service._store.close()
        self._service.toggle_release_label("local:album:x", "Like!", on=True)
        self._service.shutdown()
        # Re-open the same DB path to verify persistence.
        from tunes_player.core.library.store import LibraryStore

        store = LibraryStore(self._data_dir / "library.db")
        try:
            self.assertEqual(
                store.get_release_label_names("local:album:x"),
                frozenset({"Like!"}),
            )
        finally:
            store.close()
        # PlayerService.shutdown already closed the store; avoid double-close in tearDown.
        self._service = PlayerService(config=self._config)


class LabelSyncWriteGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        from tunes_player.core.labels_sync.service import LabelSyncService
        from tunes_player.core.library.store import LibraryStore

        self._store = LibraryStore(root / "library.db")
        self._sync_folder = root / "sync"
        self._sync_folder.mkdir()
        self._writes_ok = True
        self._service = LabelSyncService(
            library_store=self._store,
            get_enabled=lambda: True,
            get_folder=lambda: str(self._sync_folder),
            writes_available=lambda: self._writes_ok,
            device_id="testhost",
        )

    def tearDown(self) -> None:
        self._store.close()
        self._tmp.cleanup()

    def test_sync_now_skips_without_error_when_writes_unavailable(self) -> None:
        self._store.toggle_release_label("tidal:album:1", "buy", on=True)
        self._writes_ok = False
        self.assertFalse(self._service.sync_now())
        self.assertIsNone(self._service.status().last_error)
        self.assertTrue(self._service.status().pending_dirty)

    def test_schedule_sync_retries_when_writes_become_available(self) -> None:
        from tunes_player.core.labels_sync.format import SYNC_RELATIVE_PATH

        self._store.toggle_release_label("tidal:album:1", "buy", on=True)
        self._writes_ok = False
        self._service.schedule_sync()
        self.assertTrue(self._service.status().pending_dirty)
        self._writes_ok = True
        # Drive the write-retry path without waiting on the real timer.
        self._service._write_retry_run()
        sync_file = self._sync_folder / SYNC_RELATIVE_PATH
        # Allow debounce timer from schedule_sync inside _write_retry_run.
        self._service.flush()
        self.assertTrue(sync_file.is_file())
        self.assertFalse(self._service.status().pending_dirty)


if __name__ == "__main__":
    unittest.main()
