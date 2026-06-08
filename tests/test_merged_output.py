"""Tests for merged ALSA + PipeWire output listing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tunes_player.core.config import ConfigManager
from tunes_player.core.volume import VolumeEndpoint
from tunes_player.platform.linux.audio import (
    LinuxOutputController,
    _mark_preferred_default,
    create_volume_controller,
)


class MergedOutputTests(unittest.TestCase):
    def test_preferred_default_is_first_alsa(self) -> None:
        endpoints = [
            VolumeEndpoint(
                id="alsa:hw:0:0",
                name="hw:0,0",
                description="DAC",
                bit_perfect_potential="direct",
            ),
            VolumeEndpoint(
                id="48",
                name="alsa_output.pci",
                description="PW sink",
                is_default=True,
                bit_perfect_potential="capable",
            ),
        ]
        marked = _mark_preferred_default(endpoints, configured_id=None)
        self.assertTrue(marked[0].is_default)
        self.assertFalse(marked[1].is_default)

    def test_merged_controller_lists_alsa_before_sinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            controller = LinuxOutputController(config.config)
            alsa = [
                VolumeEndpoint(
                    id="alsa:hw:0:0",
                    name="hw:0,0",
                    description="ALSA DAC",
                    bit_perfect_potential="direct",
                )
            ]
            sinks = [
                VolumeEndpoint(
                    id="99",
                    name="pw-sink",
                    description="PW",
                    is_default=True,
                    bit_perfect_potential="capable",
                )
            ]
            with (
                patch.object(controller, "_alsa_volume_endpoints", return_value=alsa),
                patch.object(controller, "_list_sink_endpoints", return_value=sinks),
            ):
                listed = controller.list_endpoints()
            self.assertEqual(listed[0].id, "alsa:hw:0:0")
            self.assertEqual(listed[1].id, "99")
            self.assertTrue(listed[0].is_default)

    def test_uses_device_volume_when_alsa_mixer_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            controller = LinuxOutputController(config.config)
            with (
                patch.object(
                    controller,
                    "_alsa_volume_endpoints",
                    return_value=[
                        VolumeEndpoint(
                            id="alsa:hw:0:0",
                            name="hw:0,0",
                            description="ALSA",
                            bit_perfect_potential="direct",
                        )
                    ],
                ),
                patch.object(controller, "_list_sink_endpoints", return_value=[]),
                patch.object(controller, "_alsa_has_hardware_volume", return_value=True),
            ):
                controller.set_active_endpoint("alsa:hw:0:0")
                self.assertTrue(controller.uses_device_volume)

    def test_hdmi_pipewire_sink_has_no_device_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            controller = LinuxOutputController(config.config)
            hdmi = VolumeEndpoint(
                id="pw:Raptor Lake-P/U/H cAVS HDMI / DisplayPort 1 Output",
                name="alsa_output.pci-0000_00_1f.3.HiFi__HDMI__sink",
                description="Raptor Lake-P/U/H cAVS HDMI / DisplayPort 1 Output",
                bit_perfect_potential="capable",
                control_id="103",
            )
            with (
                patch.object(controller, "_alsa_volume_endpoints", return_value=[]),
                patch.object(controller, "_list_sink_endpoints", return_value=[hdmi]),
            ):
                controller.set_active_endpoint(hdmi.id)
                self.assertFalse(controller.uses_device_volume)

    def test_uses_software_volume_when_alsa_has_no_mixer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            controller = LinuxOutputController(config.config)
            with (
                patch.object(
                    controller,
                    "_alsa_volume_endpoints",
                    return_value=[
                        VolumeEndpoint(
                            id="alsa:hw:0:0",
                            name="hw:0,0",
                            description="ALSA",
                            bit_perfect_potential="direct",
                        )
                    ],
                ),
                patch.object(controller, "_list_sink_endpoints", return_value=[]),
                patch.object(controller, "_alsa_has_hardware_volume", return_value=False),
            ):
                controller.set_active_endpoint("alsa:hw:0:0")
                self.assertFalse(controller.uses_device_volume)
                self.assertEqual(controller.mpv_audio_device(), "alsa/hw:0,0")

    def test_exclusive_access_supported_for_usb_alsa(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            controller = LinuxOutputController(config.config)
            with (
                patch.object(
                    controller,
                    "_alsa_volume_endpoints",
                    return_value=[
                        VolumeEndpoint(
                            id="alsa:hw:1:0",
                            name="hw:1,0",
                            description="Holo USB",
                            bit_perfect_potential="direct",
                        )
                    ],
                ),
                patch.object(controller, "_list_sink_endpoints", return_value=[]),
                patch(
                    "tunes_player.platform.linux.alsa_mixer.alsa_card_is_usb",
                    return_value=True,
                ),
            ):
                controller.set_active_endpoint("alsa:hw:1:0")
                self.assertTrue(controller.exclusive_access_supported())

    def test_exclusive_access_supported_for_pci_alsa(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            controller = LinuxOutputController(config.config)
            with (
                patch.object(
                    controller,
                    "_alsa_volume_endpoints",
                    return_value=[
                        VolumeEndpoint(
                            id="alsa:hw:0:0",
                            name="hw:0,0",
                            description="ALSA",
                            bit_perfect_potential="direct",
                        )
                    ],
                ),
                patch.object(controller, "_list_sink_endpoints", return_value=[]),
                patch(
                    "tunes_player.platform.linux.alsa_mixer.alsa_card_is_usb",
                    return_value=False,
                ),
            ):
                controller.set_active_endpoint("alsa:hw:0:0")
                self.assertTrue(controller.exclusive_access_supported())

    def test_exclusive_access_not_supported_for_pw_sink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            controller = LinuxOutputController(config.config)
            with (
                patch.object(controller, "_alsa_volume_endpoints", return_value=[]),
                patch.object(
                    controller,
                    "_list_sink_endpoints",
                    return_value=[
                        VolumeEndpoint(
                            id="99",
                            name="pw-sink",
                            description="PW",
                            is_default=True,
                        )
                    ],
                ),
            ):
                controller.set_active_endpoint("99")
                self.assertFalse(controller.exclusive_access_supported())

    def test_create_volume_controller_returns_merged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            with patch(
                "tunes_player.platform.linux.audio.LinuxOutputController.list_endpoints",
                return_value=[
                    VolumeEndpoint(
                        id="alsa:hw:0:0",
                        name="hw:0,0",
                        description="ALSA",
                        is_default=True,
                        bit_perfect_potential="direct",
                    )
                ],
            ):
                controller = create_volume_controller(config.config)
            self.assertIsInstance(controller, LinuxOutputController)


if __name__ == "__main__":
    unittest.main()
