"""Tests for labels sync JSON codec and folder store."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tunes_player.core.labels_sync.folder_store import FolderRemoteStore
from tunes_player.core.labels_sync.format import (
    dumps_label_map,
    is_label_sync_document,
    loads_label_map,
    shard_relative_path,
)
from tunes_player.core.labels_sync.merge import LabelEntry
from tunes_player.core.labels_sync.store_protocol import ConflictError


class LabelsSyncFormatTests(unittest.TestCase):
    def test_roundtrip_and_catalog_collapse(self) -> None:
        label_map = {
            "tidal:album:123@cd": {
                "buy": LabelEntry(on=True, at_ns=10, by="home"),
            },
            "tidal:album:123": {
                "buy": LabelEntry(on=False, at_ns=20, by="work"),
            },
        }
        data = dumps_label_map(label_map)
        loaded = loads_label_map(data)
        self.assertIn("tidal:album:123", loaded)
        self.assertNotIn("tidal:album:123@cd", loaded)
        self.assertFalse(loaded["tidal:album:123"]["buy"].on)
        self.assertIn(b'"format": 1', data)

    def test_shard_path_and_document_detection(self) -> None:
        self.assertEqual(
            shard_relative_path("abc123"),
            "tunes-labels.abc123.json",
        )
        self.assertTrue(is_label_sync_document("tunes-labels.json"))
        self.assertTrue(is_label_sync_document("tunes-labels.abc123.json"))
        self.assertTrue(
            is_label_sync_document(
                "tunes-labels (conflicted copy 2026-08-05 123456).json"
            )
        )
        self.assertTrue(
            is_label_sync_document(
                "tunes-labels.sync-conflict-20260805-123456-ABCDEF.json"
            )
        )
        self.assertFalse(is_label_sync_document("other.json"))
        self.assertFalse(is_label_sync_document("tunes-labels.txt"))


class FolderRemoteStoreTests(unittest.TestCase):
    def test_put_get_and_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FolderRemoteStore(tmp)
            etag = store.put("tunes-labels.json", b'{"format":1,"releases":{}}\n')
            obj = store.get("tunes-labels.json")
            self.assertIsNotNone(obj)
            assert obj is not None
            self.assertEqual(obj.etag, etag)
            store.put(
                "tunes-labels.json",
                b'{"format":1,"releases":{"a":{}}}\n',
                if_match=etag,
            )
            with self.assertRaises(ConflictError):
                store.put(
                    "tunes-labels.json",
                    b"nope",
                    if_match=etag,
                )
            self.assertTrue((Path(tmp) / "tunes-labels.json").is_file())

    def test_list_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tunes-labels.json").write_text("{}\n", encoding="utf-8")
            (root / "tunes-labels.abc.json").write_text("{}\n", encoding="utf-8")
            (root / "subdir").mkdir()
            (root / "subdir" / "nested.json").write_text("{}\n", encoding="utf-8")
            store = FolderRemoteStore(tmp)
            self.assertEqual(
                store.list_names(),
                ["tunes-labels.abc.json", "tunes-labels.json"],
            )


if __name__ == "__main__":
    unittest.main()
