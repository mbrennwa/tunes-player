"""Tests for orphan mpv cleanup helpers."""

from __future__ import annotations

import unittest

from tunes_player.platform.linux.mpv_cleanup import _device_pgrep_patterns


class MpvCleanupTests(unittest.TestCase):
    def test_device_patterns_cover_mpv_alsa_forms(self) -> None:
        patterns = _device_pgrep_patterns("alsa/hw:1,0")
        self.assertIn("--audio-device=alsa/hw:1,0", patterns)
        self.assertIn("--audio-device=hw:1,0", patterns)

    def test_device_patterns_cover_plughw(self) -> None:
        patterns = _device_pgrep_patterns("alsa/plughw:0,0")
        self.assertIn("--audio-device=alsa/plughw:0,0", patterns)
        self.assertIn("--audio-device=plughw:0,0", patterns)


if __name__ == "__main__":
    unittest.main()
