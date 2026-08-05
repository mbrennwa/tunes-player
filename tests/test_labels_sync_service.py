"""End-to-end label sync folder roundtrip via LabelSyncService."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tunes_player.core.labels_sync.format import (
    SYNC_RELATIVE_PATH,
    dumps_label_map,
    loads_label_map,
    shard_relative_path,
)
from tunes_player.core.labels_sync.merge import LabelEntry
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
        self._device_a = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        self._service = LabelSyncService(
            library_store=self._store,
            get_enabled=lambda: self._enabled,
            get_folder=lambda: str(self._sync_folder),
            device_id=self._device_a,
            by_name="testhost",
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
        shard_a = self._sync_folder / shard_relative_path(self._device_a)
        self.assertTrue(shard_a.is_file())
        self.assertFalse((self._sync_folder / SYNC_RELATIVE_PATH).is_file())
        remote = loads_label_map(shard_a.read_bytes())
        self.assertTrue(remote["tidal:album:42"]["buy"].on)

        other_db = Path(self._tmp.name) / "other.db"
        other_store = LibraryStore(other_db)
        other_store.set_preserve_synced_label_orphans(True)
        device_b = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        other = LabelSyncService(
            library_store=other_store,
            get_enabled=lambda: True,
            get_folder=lambda: str(self._sync_folder),
            device_id=device_b,
            by_name="otherhost",
        )
        self.assertTrue(other.sync_now())
        self.assertEqual(
            other_store.get_release_label_names("tidal:album:42"),
            frozenset({"buy"}),
        )
        shard_b = self._sync_folder / shard_relative_path(device_b)
        self.assertTrue(shard_b.is_file())
        other_store.close()

    def test_two_devices_write_distinct_shards(self) -> None:
        self._store.toggle_release_label(
            "tidal:album:1",
            "buy",
            on=True,
            by_device="testhost",
            mark_dirty=True,
        )
        self.assertTrue(self._service.sync_now())

        other_store = LibraryStore(Path(self._tmp.name) / "other2.db")
        other_store.set_preserve_synced_label_orphans(True)
        device_b = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        other = LabelSyncService(
            library_store=other_store,
            get_enabled=lambda: True,
            get_folder=lambda: str(self._sync_folder),
            device_id=device_b,
            by_name="otherhost",
        )
        other_store.toggle_release_label(
            "tidal:album:1",
            "vinyl",
            on=True,
            by_device="otherhost",
            mark_dirty=True,
        )
        self.assertTrue(other.sync_now())
        self.assertTrue(self._service.sync_now())

        self.assertEqual(
            self._store.get_release_label_names("tidal:album:1"),
            frozenset({"buy", "vinyl"}),
        )
        self.assertEqual(
            other_store.get_release_label_names("tidal:album:1"),
            frozenset({"buy", "vinyl"}),
        )
        names = {p.name for p in self._sync_folder.iterdir() if p.is_file()}
        self.assertEqual(
            names,
            {
                shard_relative_path(self._device_a),
                shard_relative_path(device_b),
            },
        )
        other_store.close()

    def test_legacy_single_file_absorbed(self) -> None:
        legacy = {
            "tidal:album:99": {
                "buy": LabelEntry(on=True, at_ns=1_000, by="old"),
            }
        }
        (self._sync_folder / SYNC_RELATIVE_PATH).write_bytes(dumps_label_map(legacy))
        self.assertTrue(self._service.sync_now())
        self.assertEqual(
            self._store.get_release_label_names("tidal:album:99"),
            frozenset({"buy"}),
        )
        shard = self._sync_folder / shard_relative_path(self._device_a)
        self.assertTrue(shard.is_file())
        # Legacy left in place; not rewritten as the live store.
        self.assertTrue((self._sync_folder / SYNC_RELATIVE_PATH).is_file())

    def test_conflict_sibling_merged(self) -> None:
        conflict = {
            "tidal:album:7": {
                "keep": LabelEntry(on=True, at_ns=5_000, by="peer"),
            }
        }
        conflict_name = "tunes-labels (conflicted copy 2026-08-05 123456).json"
        (self._sync_folder / conflict_name).write_bytes(dumps_label_map(conflict))
        self.assertTrue(self._service.sync_now())
        self.assertEqual(
            self._store.get_release_label_names("tidal:album:7"),
            frozenset({"keep"}),
        )
        shard = loads_label_map(
            (self._sync_folder / shard_relative_path(self._device_a)).read_bytes()
        )
        self.assertTrue(shard["tidal:album:7"]["keep"].on)

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
            device_id="cccccccccccccccccccccccccccccccc",
            by_name="blank",
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
