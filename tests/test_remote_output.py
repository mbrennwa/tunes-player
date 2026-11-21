"""Remote MPD output definitions, endpoint ids and config round trip (#60)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tunes_player.core.audio_device_messages import remote_mpd_error_message
from tunes_player.core.audio_labels import endpoint_dropdown_label
from tunes_player.core.config import ConfigManager
from tunes_player.core.remote_output import (
    RemoteMpdOutput,
    is_remote_output_id,
    normalize_remote_output,
    parse_remote_outputs,
    remote_outputs_to_json,
)
from tunes_player.core.volume import (
    is_mpd_endpoint_id,
    mpd_endpoint_id,
    mpd_host_port_from_endpoint_id,
)


class EndpointIdTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        endpoint_id = mpd_endpoint_id("den.local", 6600)
        self.assertEqual(endpoint_id, "mpd:den.local:6600")
        self.assertTrue(is_mpd_endpoint_id(endpoint_id))
        self.assertEqual(mpd_host_port_from_endpoint_id(endpoint_id), ("den.local", 6600))

    def test_rejects_other_ids_and_bad_ports(self) -> None:
        self.assertFalse(is_mpd_endpoint_id("pw:sink"))
        self.assertIsNone(mpd_host_port_from_endpoint_id("pw:sink"))
        self.assertIsNone(mpd_host_port_from_endpoint_id("mpd:host:0"))
        self.assertIsNone(mpd_host_port_from_endpoint_id("mpd:host:70000"))
        self.assertIsNone(mpd_host_port_from_endpoint_id("mpd:host:abc"))


class NormalizeTests(unittest.TestCase):
    def test_accepts_valid_input(self) -> None:
        output = normalize_remote_output(" Den ", " den.local ", "6601")
        self.assertEqual(output, RemoteMpdOutput(name="Den", host="den.local", port=6601))
        self.assertEqual(output.id, "mpd:den.local:6601")

    def test_name_defaults_to_host_and_port_to_6600(self) -> None:
        output = normalize_remote_output("", "den.local", 6600)
        self.assertEqual((output.name, output.port), ("den.local", 6600))

    def test_rejects_unusable_input(self) -> None:
        self.assertIsNone(normalize_remote_output("x", "", 6600))
        self.assertIsNone(normalize_remote_output("x", "a b", 6600))
        self.assertIsNone(normalize_remote_output("x", "host:6600", 6600))
        self.assertIsNone(normalize_remote_output("x", "host", "nope"))
        self.assertIsNone(normalize_remote_output("x", "host", 65536))

    def test_endpoint_describes_a_network_output_without_bit_perfect(self) -> None:
        endpoint = RemoteMpdOutput(name="Den", host="den.local").to_endpoint()
        self.assertEqual(endpoint.id, "mpd:den.local:6600")
        self.assertEqual(endpoint.bit_perfect_potential, "none")
        self.assertEqual(endpoint_dropdown_label(endpoint), "Den (network MPD)")
        self.assertTrue(is_remote_output_id(endpoint.id))


class SerializationTests(unittest.TestCase):
    def test_json_round_trip(self) -> None:
        outputs = [
            RemoteMpdOutput(name="A", host="a.local"),
            RemoteMpdOutput(name="B", host="10.0.0.2", port=6601),
        ]
        self.assertEqual(parse_remote_outputs(remote_outputs_to_json(outputs)), outputs)

    def test_parse_skips_garbage_and_duplicates(self) -> None:
        raw = [
            {"name": "A", "host": "a.local", "port": 6600},
            {"name": "dup", "host": "a.local", "port": 6600},
            {"name": "bad", "host": "", "port": 6600},
            "nonsense",
            {"host": "b.local"},
        ]
        parsed = parse_remote_outputs(raw)
        self.assertEqual([item.host for item in parsed], ["a.local", "b.local"])
        self.assertEqual(parse_remote_outputs(None), [])


class ConfigRoundTripTests(unittest.TestCase):
    def test_remote_outputs_and_selection_persist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            config = ConfigManager(path)
            config.load()
            output = RemoteMpdOutput(name="Den", host="den.local")
            self.assertTrue(config.add_remote_output(output))
            self.assertFalse(config.add_remote_output(output))
            config.config.remote_output_id = output.id
            config.config.output_sink_id = "pw:local"
            config.save()

            reloaded = ConfigManager(path)
            reloaded.load()
            self.assertEqual(reloaded.config.remote_outputs, [output])
            self.assertEqual(reloaded.config.remote_output_id, output.id)
            self.assertEqual(reloaded.config.output_sink_id, "pw:local")

    def test_selection_of_unknown_output_is_dropped_on_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            config = ConfigManager(path)
            config.load()
            config.config.remote_output_id = "mpd:gone:6600"
            config.save()
            reloaded = ConfigManager(path)
            reloaded.load()
            self.assertIsNone(reloaded.config.remote_output_id)

    def test_removing_the_active_output_clears_the_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.json")
            config.load()
            output = RemoteMpdOutput(name="Den", host="den.local")
            config.add_remote_output(output)
            config.config.remote_output_id = output.id
            self.assertTrue(config.remove_remote_output(output.id))
            self.assertIsNone(config.config.remote_output_id)
            self.assertFalse(config.remove_remote_output(output.id))

    def test_old_config_without_remote_fields_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text('{"output_sink_id": "pw:x"}')
            config = ConfigManager(path)
            config.load()
            self.assertEqual(config.config.remote_outputs, [])
            self.assertIsNone(config.config.remote_output_id)
            self.assertEqual(config.config.output_sink_id, "pw:x")


class ErrorMessageTests(unittest.TestCase):
    def test_busy_message_is_friendly(self) -> None:
        message = remote_mpd_error_message(
            "Den", 'Failed to open ALSA device "hw:0,0": Device or resource busy'
        )
        self.assertIn("busy", message)
        self.assertIn("Den", message)

    def test_connection_message_passes_through(self) -> None:
        message = remote_mpd_error_message("Den", "Could not connect to MPD 'Den' at x:1.")
        self.assertIn("Could not connect", message)

    def test_other_errors_name_the_output(self) -> None:
        self.assertIn("Den", remote_mpd_error_message("Den", "something odd"))


if __name__ == "__main__":
    unittest.main()
