"""Config persistence for New Releases cutoff."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tunes_player.core.config import ConfigManager, normalize_new_music_within_days


class TestNewMusicWithinDays(unittest.TestCase):
    def test_normalize_clamps_and_defaults(self) -> None:
        self.assertEqual(normalize_new_music_within_days(90), 90)
        self.assertEqual(normalize_new_music_within_days(0), 1)
        self.assertEqual(normalize_new_music_within_days(999), 365)
        self.assertEqual(normalize_new_music_within_days("bad"), 90)

    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            manager = ConfigManager(path)
            manager.load()
            manager.set_new_music_within_days(45)

            other = ConfigManager(path)
            other.load()
            self.assertEqual(other.config.new_music_within_days, 45)

            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["new_music_within_days"], 45)


if __name__ == "__main__":
    unittest.main()
