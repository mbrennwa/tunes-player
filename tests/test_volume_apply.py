"""Tests for coalesced device-volume apply and subscribe foundation (#106 / #104)."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tunes_player.core.config import ConfigManager
from tunes_player.core.services import PlayerService
from tunes_player.core.volume import (
    Unsubscribe,
    VolumeEndpoint,
    VolumeListener,
    VolumeSubscriptionHub,
)
from tunes_player.core.volume_apply import _CHASE_MAX_STEP
from tunes_player.engines.mpv import MpvEngine
from tunes_player.platform.linux.audio import WpctlVolumeController


class _SlowSinkController:
    uses_device_volume = True

    def __init__(self, config: object, *, delay: float = 0.05) -> None:
        self._config = config
        self._level = 0.5
        self._delay = delay
        self._subscriptions = VolumeSubscriptionHub()
        self.set_level_calls: list[float] = []
        self._call_lock = threading.Lock()

    def available(self) -> bool:
        return True

    def get_level(self) -> float:
        return self._level

    def set_level(self, level: float) -> None:
        time.sleep(self._delay)
        clamped = max(0.0, min(1.0, level))
        with self._call_lock:
            self._level = clamped
            self.set_level_calls.append(clamped)
        self._subscriptions.notify(clamped)

    def adjust_level(self, delta: float) -> None:
        self.set_level(self._level + delta)

    def list_endpoints(self) -> list[VolumeEndpoint]:
        return [
            VolumeEndpoint(
                id="pw:speakers",
                name="speakers",
                description="Speakers",
                is_default=True,
                control_id="97",
            )
        ]

    def get_active_endpoint_id(self) -> str | None:
        return "pw:speakers"

    def set_active_endpoint(self, endpoint_id: str) -> None:
        self._config.output_sink_id = endpoint_id

    def mpv_audio_device(self) -> str | None:
        return "pulse/speakers"

    def subscribe(self, listener: VolumeListener) -> Unsubscribe:
        return self._subscriptions.subscribe(listener)

    def notify_external_level(self, level: float) -> None:
        self._subscriptions.notify(level)


class VolumeApplyTests(unittest.TestCase):
    def test_rapid_set_volume_chases_to_last_level(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            controller = _SlowSinkController(config.config, delay=0.0)
            service = PlayerService(config=config, volume_controller=controller)

            for level in (0.1, 0.2, 0.3, 0.4, 0.55):
                service.set_volume(level, notify=False)
            service.flush_pending_volume_apply()

            self.assertAlmostEqual(controller.get_level(), 0.55)
            self.assertEqual(controller.set_level_calls[-1], 0.55)
            previous = 0.5  # controller seed before chase
            for level in controller.set_level_calls:
                self.assertLessEqual(abs(level - previous), _CHASE_MAX_STEP + 1e-9)
                previous = level

    def test_set_volume_does_not_block_on_slow_controller(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            controller = _SlowSinkController(config.config, delay=0.2)
            service = PlayerService(config=config, volume_controller=controller)

            t0 = time.perf_counter()
            service.set_volume(0.42, notify=False)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.assertLess(elapsed_ms, 50)
            self.assertAlmostEqual(service.get_playback_state().volume, 0.42)
            service.flush_pending_volume_apply()
            self.assertAlmostEqual(controller.get_level(), 0.42)

    def test_external_subscribe_updates_service_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            controller = _SlowSinkController(config.config, delay=0.0)
            events: list[str] = []
            service = PlayerService(config=config, volume_controller=controller)
            service.subscribe(events.append)

            controller.notify_external_level(0.33)

            self.assertAlmostEqual(service.get_playback_state().volume, 0.33)
            self.assertIn("volume_changed", events)

    def test_outbound_apply_does_not_echo_volume_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            controller = _SlowSinkController(config.config, delay=0.0)
            events: list[str] = []
            service = PlayerService(config=config, volume_controller=controller)
            service.subscribe(events.append)

            service.set_volume(0.61, notify=False)
            service.flush_pending_volume_apply()

            self.assertNotIn("volume_changed", events)
            self.assertAlmostEqual(controller.get_level(), 0.61)

    def test_volume_gesture_ignores_inbound_stack_level(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            controller = _SlowSinkController(config.config, delay=0.0)
            events: list[str] = []
            service = PlayerService(config=config, volume_controller=controller)
            service.subscribe(events.append)
            service.set_volume(0.50, notify=False)
            service.flush_pending_volume_apply()
            events.clear()

            service.begin_volume_gesture()
            controller.notify_external_level(0.22)
            self.assertAlmostEqual(service.get_playback_state().volume, 0.50)
            self.assertNotIn("volume_changed", events)

            service.end_volume_gesture()
            controller.notify_external_level(0.22)
            self.assertAlmostEqual(service.get_playback_state().volume, 0.22)
            self.assertIn("volume_changed", events)

    def test_volume_gesture_chases_but_ignores_inbound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            controller = _SlowSinkController(config.config, delay=0.0)
            service = PlayerService(config=config, volume_controller=controller)
            service.set_volume(0.50, notify=False)
            service.flush_pending_volume_apply()
            controller.set_level_calls.clear()

            service.begin_volume_gesture()
            for level in (0.20, 0.08, 0.12, 0.05):
                service.set_volume(level, notify=False)
            service.flush_pending_volume_apply()
            self.assertAlmostEqual(controller.set_level_calls[-1], 0.05)
            self.assertAlmostEqual(service.get_playback_state().volume, 0.05)
            previous = 0.50
            for level in controller.set_level_calls:
                self.assertLessEqual(abs(level - previous), _CHASE_MAX_STEP + 1e-9)
                previous = level

            controller.notify_external_level(0.99)
            self.assertAlmostEqual(service.get_playback_state().volume, 0.05)

            service.end_volume_gesture()
            controller.notify_external_level(0.33)
            self.assertAlmostEqual(service.get_playback_state().volume, 0.33)

    def test_chase_retargets_while_worker_runs(self) -> None:
        """A newer schedule_apply becomes the chase target mid-flight."""
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            controller = _SlowSinkController(config.config, delay=0.02)
            service = PlayerService(config=config, volume_controller=controller)
            service.set_volume(0.50, notify=False)
            service.flush_pending_volume_apply()
            controller.set_level_calls.clear()

            service.set_volume(0.10, notify=False)
            time.sleep(0.05)
            service.set_volume(0.90, notify=False)
            service.flush_pending_volume_apply(timeout=5.0)

            self.assertAlmostEqual(controller.get_level(), 0.90)
            self.assertAlmostEqual(controller.set_level_calls[-1], 0.90)
            previous = 0.50
            for level in controller.set_level_calls:
                self.assertLessEqual(abs(level - previous), _CHASE_MAX_STEP + 1e-9)
                previous = level

    def test_set_level_sync_snaps_without_chase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            controller = _SlowSinkController(config.config, delay=0.0)
            service = PlayerService(config=config, volume_controller=controller)
            controller.set_level_calls.clear()

            service._set_device_volume_sync(0.12)

            self.assertEqual(controller.set_level_calls, [0.12])
            self.assertAlmostEqual(controller.get_level(), 0.12)


class WpctlTargetCacheTests(unittest.TestCase):
    def test_set_level_uses_cached_endpoints_not_fresh_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            config.config.output_sink_id = "pw:speakers"
            controller = WpctlVolumeController(config.config)
            controller._cached_endpoints = [
                VolumeEndpoint(
                    id="pw:speakers",
                    name="speakers",
                    description="Speakers",
                    is_default=True,
                    control_id="97",
                )
            ]
            runs: list[list[str]] = []

            def fake_run(args: list[str], *, check: bool = False):
                runs.append(list(args))
                result = MagicMock()
                result.stdout = ""
                return result

            with patch.object(controller, "_run", side_effect=fake_run):
                with patch.object(controller, "_list_endpoints") as list_raw:
                    controller.set_level(0.4)
                    list_raw.assert_not_called()

            self.assertTrue(any(args[:2] == ["wpctl", "set-volume"] for args in runs))
            self.assertFalse(any(args == ["wpctl", "status"] for args in runs))
            set_args = next(args for args in runs if args[:2] == ["wpctl", "set-volume"])
            self.assertEqual(set_args[2], "97")


class MpvBitPerfectGuardTests(unittest.TestCase):
    def test_repeated_set_bit_perfect_true_skips_property_writes(self) -> None:
        engine = object.__new__(MpvEngine)
        engine._unity_gain = True
        engine._volume = 1.0
        engine._use_device_output = True
        engine._software_volume = False
        engine._set_property = MagicMock()
        engine._apply_software_volume = MagicMock()
        engine._sync_direct_alsa_buffer_policy_for_volume_mode = MagicMock()

        engine.set_bit_perfect(True)
        engine.set_bit_perfect(True)

        engine._set_property.assert_not_called()


if __name__ == "__main__":
    unittest.main()
