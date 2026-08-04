"""Label sync store behavior: dirty, tombstones, prune gating."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tunes_player.core.labels_sync.merge import LabelEntry
from tunes_player.core.library.store import LibraryStore


class LabelSyncStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "library.db"
        self._store = LibraryStore(self._db_path)

    def tearDown(self) -> None:
        self._store.close()
        self._tmp.cleanup()

    def test_toggle_writes_tombstone_and_dirty(self) -> None:
        self._store.toggle_release_label(
            "tidal:album:1",
            "buy",
            on=True,
            by_device="home",
            mark_dirty=True,
        )
        self.assertTrue(self._store.has_dirty_label_sync_rows())
        self._store.toggle_release_label(
            "tidal:album:1",
            "buy",
            on=False,
            by_device="home",
            mark_dirty=True,
        )
        exported = self._store.export_label_sync_map()
        self.assertFalse(exported["tidal:album:1"]["buy"].on)
        self.assertEqual(
            self._store.get_release_label_names("tidal:album:1"),
            frozenset(),
        )

    def test_preserve_local_orphans_when_sync_enabled(self) -> None:
        self._store.set_preserve_synced_label_orphans(True)
        self._store.toggle_release_label("local:album:ghost", "orphan", on=True)
        self._store.reconnect()
        self.assertEqual(self._store.list_user_label_names(), ("orphan",))
        self.assertEqual(
            self._store.get_release_label_names("local:album:ghost"),
            frozenset({"orphan"}),
        )

    def test_prune_local_orphans_when_sync_disabled(self) -> None:
        self._store.set_preserve_synced_label_orphans(False)
        self._store.toggle_release_label("local:album:ghost", "orphan", on=True)
        self._store.reconnect()
        self.assertEqual(self._store.list_user_label_names(), ())

    def test_apply_and_clear_dirty(self) -> None:
        self._store.apply_label_sync_map(
            {
                "tidal:album:9": {
                    "vinyl": LabelEntry(on=True, at_ns=50, by="work"),
                    "buy": LabelEntry(on=False, at_ns=60, by="work"),
                }
            },
            clear_dirty=True,
        )
        self.assertFalse(self._store.has_dirty_label_sync_rows())
        self.assertEqual(
            self._store.get_release_label_names("tidal:album:9"),
            frozenset({"vinyl"}),
        )
        exported = self._store.export_label_sync_map()
        self.assertFalse(exported["tidal:album:9"]["buy"].on)


if __name__ == "__main__":
    unittest.main()
