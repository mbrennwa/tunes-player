"""Tests for stable PipeWire output sink ids in config."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tunes_player.core.config import ConfigManager
from tunes_player.core.volume import VolumeEndpoint, pipewire_endpoint_id
from tunes_player.platform.linux.audio import LinuxOutputController


class OutputSinkPersistenceTests(unittest.TestCase):
    def test_migrates_legacy_numeric_wpctl_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            config.config.output_sink_id = "48"
            config.save()
            controller = LinuxOutputController(config.config)
            sinks = [
                VolumeEndpoint(
                    id=pipewire_endpoint_id("alsa_output.pci.analog-stereo"),
                    name="alsa_output.pci.analog-stereo",
                    description="HD Audio Controller Analog Stereo",
                    control_id="48",
                )
            ]
            with (
                patch.object(controller, "_alsa_volume_endpoints", return_value=[]),
                patch.object(controller, "_list_sink_endpoints", return_value=sinks),
            ):
                self.assertTrue(controller.normalize_output_sink_config())
            self.assertEqual(
                config.config.output_sink_id,
                pipewire_endpoint_id("alsa_output.pci.analog-stereo"),
            )

    def test_migrates_legacy_description_based_pw_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            legacy = pipewire_endpoint_id("HD Audio Controller Analog Stereo")
            config.config.output_sink_id = legacy
            controller = LinuxOutputController(config.config)
            sinks = [
                VolumeEndpoint(
                    id=pipewire_endpoint_id("alsa_output.pci-0000_01_01.0.analog-stereo"),
                    name="alsa_output.pci-0000_01_01.0.analog-stereo",
                    description="HD Audio Controller Analog Stereo",
                    control_id="52",
                )
            ]
            with (
                patch.object(controller, "_alsa_volume_endpoints", return_value=[]),
                patch.object(controller, "_list_sink_endpoints", return_value=sinks),
            ):
                endpoints = controller.list_endpoints()
            self.assertEqual(
                config.config.output_sink_id,
                pipewire_endpoint_id("alsa_output.pci-0000_01_01.0.analog-stereo"),
            )
            self.assertTrue(endpoints[0].is_default)


if __name__ == "__main__":
    unittest.main()
