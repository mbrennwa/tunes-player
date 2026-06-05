"""Tests for PipeWire exclusive session suspend parsing."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from tunes_player.platform.linux import exclusive_session
from tunes_player.platform.linux.exclusive_session import ExclusiveSession, _iter_nodes


_PW_LS_SAMPLE = """
	id 48, type PipeWire:Interface:Node
		alsa.card = "0"
		media.class = "Audio/Device"
	id 52, type PipeWire:Interface:Node
		alsa.card = "0"
		media.class = "Audio/Sink"
"""


class ExclusiveSessionTests(unittest.TestCase):
    def test_iter_nodes_finds_sink_on_card(self) -> None:
        nodes = list(_iter_nodes(_PW_LS_SAMPLE))
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[1], (52, 0, "Audio/Sink"))

    def test_acquire_suspends_matching_sink(self) -> None:
        session = ExclusiveSession(card=0)
        with patch.object(session, "_set_suspend", return_value=True) as suspend:
            with patch("shutil.which", return_value="/usr/bin/pw-cli"):
                with patch(
                    "subprocess.run",
                    return_value=type(
                        "R",
                        (),
                        {"returncode": 0, "stdout": _PW_LS_SAMPLE},
                    )(),
                ):
                    session.acquire()
        self.assertIn(52, session._suspended_ids)
        suspend.assert_called()

    def test_acquire_caches_pipewire_unavailable(self) -> None:
        exclusive_session._pw_cli_available = None
        session = ExclusiveSession(card=0)
        with patch("shutil.which", return_value="/usr/bin/pw-cli"):
            with patch(
                "subprocess.run",
                side_effect=subprocess.CalledProcessError(
                    255,
                    ["pw-cli", "ls", "Node"],
                    stderr='Error: "failed to connect: Host is down"',
                ),
            ) as run:
                self.assertFalse(session.acquire())
                self.assertFalse(session.acquire())
        self.assertEqual(run.call_count, 1)
        exclusive_session._pw_cli_available = None


if __name__ == "__main__":
    unittest.main()
