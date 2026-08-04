"""Tests for labels sync JSON codec and folder store."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tunes_player.core.labels_sync.folder_store import FolderRemoteStore
from tunes_player.core.labels_sync.format import dumps_label_map, loads_label_map
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


if __name__ == "__main__":
    unittest.main()
