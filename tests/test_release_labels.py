"""User-defined release label store tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tunes_player.core.library.store import LibraryStore


class ReleaseLabelStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "library.db"
        self._store = LibraryStore(self._db_path)

    def tearDown(self) -> None:
        self._store.close()
        self._tmp.cleanup()

    def test_ensure_user_label_case_insensitive(self) -> None:
        first = self._store.ensure_user_label("Listen Later")
        second = self._store.ensure_user_label("listen later")
        self.assertEqual(first, "Listen Later")
        self.assertEqual(second, "Listen Later")
        self.assertEqual(self._store.list_user_label_names(), ())
        self._store.toggle_release_label("local:album:1", "Listen Later", on=True)
        self.assertEqual(self._store.list_user_label_names(), ("Listen Later",))

    def test_toggle_and_query_release_labels(self) -> None:
        release_id = "local:release:test"
        self._store.toggle_release_label(release_id, "buy", on=True)
        self._store.toggle_release_label(release_id, "listen", on=True)
        self.assertEqual(
            self._store.get_release_label_names(release_id),
            frozenset({"buy", "listen"}),
        )
        self.assertEqual(
            self._store.list_user_label_names(),
            ("buy", "listen"),
        )
        self._store.toggle_release_label(release_id, "buy", on=False)
        self.assertEqual(
            self._store.get_release_label_names(release_id),
            frozenset({"listen"}),
        )
        self.assertEqual(self._store.list_user_label_names(), ("listen",))

    def test_prune_stale_local_release_labels(self) -> None:
        self._store.toggle_release_label("local:album:ghost", "orphan", on=True)
        self.assertEqual(self._store.list_user_label_names(), ("orphan",))
        self._store.reconnect()
        self.assertEqual(self._store.list_user_label_names(), ())
        self.assertEqual(
            self._store.get_release_label_names("local:album:ghost"),
            frozenset(),
        )

    def test_prune_invalid_streaming_release_labels(self) -> None:
        self._store.toggle_release_label("tidal:missing", "buy", on=True)
        self.assertEqual(self._store.list_user_label_names(), ("buy",))
        self._store.reconnect()
        self.assertEqual(self._store.list_user_label_names(), ())

    def test_set_release_labels_replaces_existing(self) -> None:
        release_id = "tidal:album:123"
        self._store.toggle_release_label(release_id, "old", on=True)
        self._store.set_release_labels(release_id, frozenset({"new", "fresh"}))
        self.assertEqual(
            self._store.get_release_label_names(release_id),
            frozenset({"new", "fresh"}),
        )

    def test_list_flagged_release_ids_orders_by_latest_tag(self) -> None:
        self._store.toggle_release_label("local:a", "one", on=True)
        self._store.toggle_release_label("local:b", "two", on=True)
        self._store.toggle_release_label("local:a", "three", on=True)
        flagged = self._store.list_flagged_release_ids()
        self.assertEqual(flagged[0], "local:a")
        self.assertIn("local:b", flagged)

    def test_labels_for_release_ids_batch_lookup(self) -> None:
        self._store.toggle_release_label("local:a", "rock", on=True)
        self._store.toggle_release_label("local:b", "jazz", on=True)
        mapping = self._store.labels_for_release_ids(["local:a", "local:b", "local:c"])
        self.assertEqual(mapping["local:a"], frozenset({"rock"}))
        self.assertEqual(mapping["local:b"], frozenset({"jazz"}))
        self.assertEqual(mapping["local:c"], frozenset())

    def test_prune_is_noop_when_write_connection_closed(self) -> None:
        self._store.toggle_release_label("tidal:album:1", "buy", on=True)
        self._store.close()
        # Must not raise (scan holds the DB; label menu still needs to open).
        self._store.prune_release_label_tables()
        self.assertEqual(self._store.list_user_label_names(), ("buy",))

    def test_toggle_works_when_write_connection_closed(self) -> None:
        self._store.close()
        self._store.toggle_release_label("tidal:album:2", "vinyl", on=True)
        self.assertEqual(
            self._store.get_release_label_names("tidal:album:2"),
            frozenset({"vinyl"}),
        )
        self.assertTrue(self._store.has_dirty_label_sync_rows())


if __name__ == "__main__":
    unittest.main()
