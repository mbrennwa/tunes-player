"""Tests for mpv logging and diagnostic helpers."""

from __future__ import annotations

import unittest
from pathlib import Path

from tunes_player.core.playback.mpv_logging import (
    MPV_LOG_FILE_NAME,
    format_action_provenance,
    mpv_log_path,
)


class MpvLoggingTests(unittest.TestCase):
    def test_mpv_log_path(self) -> None:
        data_dir = Path("/tmp/tunes-data")
        self.assertEqual(mpv_log_path(data_dir), data_dir / MPV_LOG_FILE_NAME)

    def test_format_action_provenance_includes_caller(self) -> None:
        def nested() -> str:
            return format_action_provenance(depth=2, skip=1)

        provenance = nested()
        self.assertIn("nested(", provenance)
        self.assertIn("test_mpv_logging.py", provenance)


if __name__ == "__main__":
    unittest.main()
