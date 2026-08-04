"""End-to-end label sync folder roundtrip via LabelSyncService."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tunes_player.core.labels_sync.format import SYNC_RELATIVE_PATH, loads_label_map
from tunes_player.core.labels_sync.service import LabelSyncService
from tunes_player.core.library.store import LibraryStore
from tunes_player.core.release_quality_tiles import parse_catalog_release_id


class LabelSyncServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._db_path = root / "library.db"
        self._sync_folder = root / "sync"
        self._sync_folder.mkdir()
        self._store = LibraryStore(self._db_path)
        self._store.set_preserve_synced_label_orphans(True)
        self._enabled = True
        self._service = LabelSyncService(
            library_store=self._store,
            get_enabled=lambda: self._enabled,
            get_folder=lambda: str(self._sync_folder),
            device_id="testhost",
        )

    def tearDown(self) -> None:
        self._store.close()
        self._tmp.cleanup()

    def test_sync_roundtrip_between_stores(self) -> None:
        self._store.toggle_release_label(
            "tidal:album:42@hi_res",
            "buy",
            on=True,
            by_device="testhost",
            mark_dirty=True,
        )
        # Normalize like PlayerService does.
        catalog = parse_catalog_release_id("tidal:album:42@hi_res")
        self.assertEqual(catalog, "tidal:album:42")
        # Re-write under catalog id (service path).
        self._store.toggle_release_label(
            catalog,
            "buy",
            on=True,
            by_device="testhost",
            mark_dirty=True,
        )
        self.assertTrue(self._service.sync_now())
        sync_file = self._sync_folder / SYNC_RELATIVE_PATH
        self.assertTrue(sync_file.is_file())
        remote = loads_label_map(sync_file.read_bytes())
        self.assertTrue(remote["tidal:album:42"]["buy"].on)

        other_db = Path(self._tmp.name) / "other.db"
        other_store = LibraryStore(other_db)
        other_store.set_preserve_synced_label_orphans(True)
        other = LabelSyncService(
            library_store=other_store,
            get_enabled=lambda: True,
            get_folder=lambda: str(self._sync_folder),
            device_id="otherhost",
        )
        self.assertTrue(other.sync_now())
        self.assertEqual(
            other_store.get_release_label_names("tidal:album:42"),
            frozenset({"buy"}),
        )
        other_store.close()

    def test_export_import(self) -> None:
        self._store.toggle_release_label(
            "qobuz:album:9",
            "vinyl",
            on=True,
            by_device="testhost",
            mark_dirty=True,
        )
        export_path = Path(self._tmp.name) / "export.json"
        self._service.export_to(export_path)
        blank = LibraryStore(Path(self._tmp.name) / "blank.db")
        blank_service = LabelSyncService(
            library_store=blank,
            get_enabled=lambda: False,
            get_folder=lambda: None,
            device_id="blank",
        )
        blank_service.import_from(export_path)
        self.assertEqual(
            blank.get_release_label_names("qobuz:album:9"),
            frozenset({"vinyl"}),
        )
        blank.close()

    def test_noop_sync_does_not_rewrite_when_unchanged(self) -> None:
        self._store.toggle_release_label(
            "tidal:album:7",
            "buy",
            on=True,
            by_device="testhost",
            mark_dirty=True,
        )
        self.assertTrue(self._service.sync_now())
        # Second sync with no edits must be a no-op success.
        applied = {"count": 0}
        original = self._store.apply_label_sync_map

        def wrapped(*args, **kwargs):
            applied["count"] += 1
            return original(*args, **kwargs)

        self._store.apply_label_sync_map = wrapped  # type: ignore[method-assign]
        try:
            self.assertTrue(self._service.sync_now())
            self.assertEqual(applied["count"], 0)
        finally:
            self._store.apply_label_sync_map = original  # type: ignore[method-assign]


if __name__ == "__main__":
    unittest.main()
