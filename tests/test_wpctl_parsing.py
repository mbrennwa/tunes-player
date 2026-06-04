"""Tests for wpctl status sink parsing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tunes_player.core.config import ConfigManager
from unittest.mock import patch

from tunes_player.platform.linux.audio import (
    NullVolumeController,
    _parse_wpctl_sink_line,
    _parse_wpctl_status_sinks,
)

_WPCTL_SAMPLE = """PipeWire 'pipewire-0' [1.0.0, user@vm, cookie:1]

Audio
 ├─ Devices:
 │      42. HD Audio Controller                 [alsa]
 ├─ Sinks:
 │  *   48. HD Audio Controller Analog Stereo   [vol: 0.50]
 │      68. alsa_output.pci.hdmi-stereo          [vol: 0.40]
 ├─ Sources:
 │  *  101. Microphone                         [vol: 0.74]
"""


class WpctlParsingTests(unittest.TestCase):
    def test_parse_sink_line_with_tree_chars(self) -> None:
        line = " │  *   48. HD Audio Controller Analog Stereo   [vol: 0.50]"
        match = _parse_wpctl_sink_line(line)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group("id"), "48")
        self.assertEqual(match.group("default"), "*")
        self.assertIn("HD Audio", match.group("name"))

    def test_parse_status_block_finds_sinks(self) -> None:
        def fake_inspect(sink_id: str) -> tuple[str | None, str | None]:
            mapping = {
                "48": ("alsa_output.pci.analog-stereo", "HD Audio Controller Analog Stereo"),
                "68": ("alsa_output.pci.hdmi-stereo", "HDMI Stereo"),
            }
            return mapping.get(sink_id, (None, None))

        with patch(
            "tunes_player.platform.linux.audio._wpctl_inspect_sink",
            side_effect=fake_inspect,
        ):
            endpoints = _parse_wpctl_status_sinks(_WPCTL_SAMPLE)
        self.assertEqual(len(endpoints), 2)
        self.assertEqual(endpoints[0].id, "pw:alsa_output.pci.analog-stereo")
        self.assertEqual(endpoints[0].name, "alsa_output.pci.analog-stereo")
        self.assertEqual(endpoints[0].description, "HD Audio Controller Analog Stereo")
        self.assertEqual(endpoints[0].control_id, "48")
        self.assertTrue(endpoints[0].is_default)
        self.assertEqual(endpoints[1].id, "pw:alsa_output.pci.hdmi-stereo")
        self.assertEqual(endpoints[1].bit_perfect_potential, "capable")

    def test_null_controller_lists_system_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            controller = NullVolumeController(config.config)
            endpoints = controller.list_endpoints()
            self.assertEqual(len(endpoints), 1)
            self.assertEqual(endpoints[0].description, "System default")


if __name__ == "__main__":
    unittest.main()
