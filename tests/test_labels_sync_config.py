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

            other = ConfigManager(path)
            other.load()
            self.assertTrue(other.config.labels_sync_enabled)
            self.assertEqual(
                other.config.labels_sync_folder,
                str(Path(tmp).resolve()),
            )
            self.assertEqual(other.config.labels_sync_last_success_at, 123.0)
            self.assertEqual(other.config.labels_sync_last_error, "boom")

            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(raw["labels_sync_enabled"])
            self.assertIn("labels_sync_folder", raw)


if __name__ == "__main__":
    unittest.main()
