"""Tests for derived audio output policy (unity gain vs software volume)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tunes_player.core.audio_labels import (
    classify_sink_potential,
    endpoint_display_label,
    endpoint_dropdown_label,
)
from tunes_player.core.volume import SYSTEM_DEFAULT_SINK_ID
from tunes_player.core.config import ConfigManager
from tunes_player.core.services import PlayerService
from tunes_player.core.volume import VolumeEndpoint, derive_volume_mode


class _SinkVolumeController:
    uses_device_volume = True

    def __init__(self, config: object) -> None:
        self._config = config
        self._level = 0.5

    def available(self) -> bool:
        return True

    def get_level(self) -> float:
        return self._level

    def set_level(self, level: float) -> None:
        self._level = level

    def adjust_level(self, delta: float) -> None:
        self._level = max(0.0, min(1.0, self._level + delta))

    def list_endpoints(self) -> list[VolumeEndpoint]:
        return [
            VolumeEndpoint(
                id="pw:alsa_output.usb-Foo",
                name="alsa_output.usb-Foo",
                description="USB DAC",
                is_default=True,
                bit_perfect_potential="capable",
                control_id="42",
            )
        ]

    def get_active_endpoint_id(self) -> str | None:
        return "pw:alsa_output.usb-Foo"

    def set_active_endpoint(self, endpoint_id: str) -> None:
        self._config.output_sink_id = endpoint_id

    def mpv_audio_device(self) -> str | None:
        return "pulse/alsa_output.usb-Foo"


class AudioPolicyTests(unittest.TestCase):
    def test_classify_usb_sink_as_capable(self) -> None:
        self.assertEqual(
            classify_sink_potential(
                name="alsa_output.usb-Foo.bar",
                description="USB Audio",
            ),
            "capable",
        )

    def test_classify_virtual_sink_as_none(self) -> None:
        self.assertEqual(
            classify_sink_potential(name="easyeffects_sink", description="Easy Effects"),
            "none",
        )

    def test_system_default_dropdown_label_has_no_suffix(self) -> None:
        endpoint = VolumeEndpoint(
            id=SYSTEM_DEFAULT_SINK_ID,
            name="default",
            description="System default",
        )
        self.assertEqual(endpoint_dropdown_label(endpoint), "System default")

    def test_endpoint_display_label_alsa_bit_perfect(self) -> None:
        endpoint = VolumeEndpoint(
            id="alsa:hw:1:0",
            name="hw:1,0",
            description="My DAC",
            bit_perfect_potential="direct",
        )
        self.assertEqual(
            endpoint_display_label(endpoint),
            "My DAC · bit perfect",
        )

    def test_endpoint_display_label_pipewire_has_no_suffix(self) -> None:
        endpoint = VolumeEndpoint(
            id="pw:alsa_output.usb-x",
            name="alsa_output.usb-x",
            description="My DAC",
            bit_perfect_potential="capable",
        )
        self.assertEqual(endpoint_display_label(endpoint), "My DAC")

    def test_derive_volume_mode(self) -> None:
        self.assertEqual(
            derive_volume_mode(device_volume=True, mpv_soft_volume=False),
            "hardware",
        )
        self.assertEqual(
            derive_volume_mode(device_volume=False, mpv_soft_volume=True),
            "software",
        )
        self.assertEqual(
            derive_volume_mode(device_volume=False, mpv_soft_volume=False),
            "fixed",
        )

    def test_unity_gain_when_device_volume_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            service = PlayerService(config=config, volume_controller=_SinkVolumeController(config.config))
            state = service.get_playback_state()
            self.assertTrue(state.device_volume)
            self.assertFalse(state.bit_perfect_playback)
            self.assertFalse(state.mpv_soft_volume)
            self.assertEqual(state.volume_mode, "hardware")
            self.assertTrue(service.volume_adjustable())

    def test_software_volume_when_no_sink_and_fallback_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            service = PlayerService(config=config, volume_controller=None)
            state = service.get_playback_state()
            self.assertFalse(state.device_volume)
            self.assertFalse(state.bit_perfect_playback)
            self.assertTrue(state.mpv_soft_volume)
            self.assertEqual(state.volume_mode, "software")
            self.assertTrue(service.volume_adjustable())

    def test_no_volume_control_when_fallback_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            config.config.allow_software_volume_fallback = False
            config.save()
            service = PlayerService(config=config, volume_controller=None)
            state = service.get_playback_state()
            self.assertTrue(state.no_volume_control)
            self.assertFalse(state.bit_perfect_playback)
            self.assertEqual(state.volume_mode, "fixed")
            self.assertFalse(service.volume_adjustable())

    def test_volume_mode_override_fixed_despite_device_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            service = PlayerService(config=config, volume_controller=_SinkVolumeController(config.config))
            service.set_volume_mode("fixed")
            state = service.get_playback_state()
            self.assertTrue(state.device_volume)
            self.assertEqual(state.volume_mode, "fixed")
            self.assertFalse(service.volume_adjustable())

    def test_hardware_to_fixed_resets_sink_and_app_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            controller = _SinkVolumeController(config.config)
            service = PlayerService(config=config, volume_controller=controller)
            service.set_volume(0.5)
            self.assertEqual(controller.get_level(), 0.5)

            service.set_volume_mode("fixed")

            self.assertEqual(controller.get_level(), 1.0)
            self.assertEqual(service.get_playback_state().volume, 1.0)

    def test_fixed_to_hardware_keeps_sink_at_full(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            controller = _SinkVolumeController(config.config)
            service = PlayerService(config=config, volume_controller=controller)
            service.set_volume(0.5)
            service.set_volume_mode("fixed")
            self.assertEqual(controller.get_level(), 1.0)

            service.set_volume_mode("hardware")

            self.assertEqual(controller.get_level(), 1.0)

    def test_hardware_to_software_resets_sink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            controller = _SinkVolumeController(config.config)
            service = PlayerService(config=config, volume_controller=controller)
            service.set_volume(0.5)
            self.assertEqual(controller.get_level(), 0.5)

            service.set_volume_mode("software")

            self.assertEqual(controller.get_level(), 1.0)

    def test_software_to_hardware_pushes_volume_to_sink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            config.config.volume_control_mode = "software"
            config.config.allow_software_volume_fallback = True
            config.save()
            controller = _SinkVolumeController(config.config)
            service = PlayerService(config=config, volume_controller=controller)
            service.set_volume(0.4)
            self.assertEqual(service.get_playback_state().volume, 0.4)

            service.set_volume_mode("hardware")

            self.assertAlmostEqual(controller.get_level(), 0.4, places=4)

    def test_startup_fixed_mode_normalizes_sink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            config.config.volume_control_mode = "fixed"
            config.save()
            controller = _SinkVolumeController(config.config)
            controller.set_level(0.5)

            service = PlayerService(config=config, volume_controller=controller)

            self.assertEqual(controller.get_level(), 1.0)
            self.assertEqual(service.get_playback_state().volume, 1.0)

    def test_set_volume_noop_when_fixed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            config.config.allow_software_volume_fallback = False
            config.save()
            service = PlayerService(config=config, volume_controller=None)
            service.set_volume(0.25)
            self.assertEqual(service.get_playback_state().volume, 0.72)

    def test_output_using_fallback_when_saved_sink_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            config.config.output_sink_id = "999"
            config.save()
            controller = _SinkVolumeController(config.config)
            service = PlayerService(config=config, volume_controller=controller)
            self.assertTrue(service.get_playback_state().output_using_fallback)

    def test_set_active_endpoint_persists_config_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            controller = _SinkVolumeController(config.config)
            controller.set_active_endpoint("pw:alsa_output.usb-Foo")
            self.assertEqual(config.config.output_sink_id, "pw:alsa_output.usb-Foo")


if __name__ == "__main__":
    unittest.main()
