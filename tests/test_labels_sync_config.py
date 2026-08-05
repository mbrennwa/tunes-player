"""Config roundtrip for labels sync settings."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tunes_player.core.config import ConfigManager


class LabelsSyncConfigTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            manager = ConfigManager(path)
            manager.load()
            manager.set_labels_sync_enabled(True)
            manager.set_labels_sync_folder(tmp)
            manager.set_labels_sync_status(123.0, "boom")
            device_id = manager.ensure_labels_sync_device_id()
            self.assertTrue(device_id)
            self.assertEqual(manager.ensure_labels_sync_device_id(), device_id)

            other = ConfigManager(path)
            other.load()
            self.assertTrue(other.config.labels_sync_enabled)
            self.assertEqual(
                other.config.labels_sync_folder,
                str(Path(tmp).resolve()),
            )
            self.assertEqual(other.config.labels_sync_last_success_at, 123.0)
            self.assertEqual(other.config.labels_sync_last_error, "boom")
            self.assertEqual(other.config.labels_sync_device_id, device_id)

            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(raw["labels_sync_enabled"])
            self.assertIn("labels_sync_folder", raw)
            self.assertEqual(raw["labels_sync_device_id"], device_id)

    def test_ensure_device_id_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            manager = ConfigManager(path)
            manager.load()
            first = manager.ensure_labels_sync_device_id()
            second = manager.ensure_labels_sync_device_id()
            self.assertEqual(first, second)
            self.assertEqual(len(first), 32)


if __name__ == "__main__":
    unittest.main()
