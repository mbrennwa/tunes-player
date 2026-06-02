"""Config persistence for Qobuz credentials."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tunes_player.core.config import ConfigManager


class TestQobuzConfig(unittest.TestCase):
    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            manager = ConfigManager(path)
            manager.load()
            manager.config.qobuz_app_id = "123456789"
            manager.config.qobuz_app_secret = "abcdef0123456789abcdef0123456789"
            manager.config.qobuz_stream_format_id = 6
            manager.save()

            other = ConfigManager(path)
            other.load()
            self.assertEqual(other.config.qobuz_app_id, "123456789")
            self.assertEqual(other.config.qobuz_app_secret, "abcdef0123456789abcdef0123456789")
            self.assertEqual(other.config.qobuz_stream_format_id, 6)

            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("qobuz_app_id", raw)
            self.assertNotIn("user_auth_token", raw)


if __name__ == "__main__":
    unittest.main()
