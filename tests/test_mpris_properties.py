"""MPRIS property Set payload unwrapping."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import tunes_player.gi_bootstrap  # noqa: F401
from gi.repository import GLib

from tunes_player.core.config import ConfigManager
from tunes_player.core.services import PlayerService
from tunes_player.platform.linux.mpris import MprisService, _unwrap_dbus_value


class UnwrapDbusValueTests(unittest.TestCase):
    def test_unwraps_variant(self) -> None:
        self.assertEqual(_unwrap_dbus_value(GLib.Variant("d", 0.25)), 0.25)

    def test_passes_through_already_unpacked(self) -> None:
        self.assertEqual(_unwrap_dbus_value(0.25), 0.25)
        self.assertEqual(_unwrap_dbus_value("None"), "None")
        self.assertIs(_unwrap_dbus_value(True), True)


class MprisSetVolumeTests(unittest.TestCase):
    def test_set_volume_accepts_float_from_properties_set_unpack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            service = PlayerService(config=config, volume_controller=None)
            mpris = MprisService(service, on_raise=lambda: None, on_quit=lambda: None)
            mpris._set_property_value(
                "org.mpris.MediaPlayer2.Player",
                "Volume",
                0.42,
            )
            self.assertAlmostEqual(service.get_playback_state().volume, 0.42)

    def test_set_volume_accepts_glib_variant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            service = PlayerService(config=config, volume_controller=None)
            mpris = MprisService(service, on_raise=lambda: None, on_quit=lambda: None)
            mpris._set_property_value(
                "org.mpris.MediaPlayer2.Player",
                "Volume",
                GLib.Variant("d", 0.33),
            )
            self.assertAlmostEqual(service.get_playback_state().volume, 0.33)

    def test_properties_set_unpack_shape_matches_float(self) -> None:
        """Simulate Properties.Set (ssv) unpack — nested v becomes a float."""
        parameters = GLib.Variant(
            "(ssv)",
            (
                "org.mpris.MediaPlayer2.Player",
                "Volume",
                GLib.Variant("d", 0.55),
            ),
        )
        _interface, _name, value = parameters.unpack()
        self.assertIsInstance(value, float)
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            service = PlayerService(config=config, volume_controller=None)
            mpris = MprisService(service, on_raise=lambda: None, on_quit=lambda: None)
            mpris._set_property_value(
                "org.mpris.MediaPlayer2.Player",
                "Volume",
                value,
            )
            self.assertAlmostEqual(service.get_playback_state().volume, 0.55)


if __name__ == "__main__":
    unittest.main()
