"""Tests for inbound stack volume watcher (#104)."""

from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

from tunes_player.platform.linux.volume_watch import (
    StackVolumeWatcher,
    pactl_subscribe_argv,
    pactl_subscribe_is_relevant,
)


class PactlSubscribeParsingTests(unittest.TestCase):
    def test_sink_change_is_relevant(self) -> None:
        self.assertTrue(
            pactl_subscribe_is_relevant("Event 'change' on sink #42")
        )

    def test_server_change_is_relevant(self) -> None:
        self.assertTrue(
            pactl_subscribe_is_relevant("Event 'change' on server #0")
        )

    def test_source_change_is_ignored(self) -> None:
        self.assertFalse(
            pactl_subscribe_is_relevant("Event 'change' on source #3")
        )

    def test_new_sink_is_ignored(self) -> None:
        self.assertFalse(
            pactl_subscribe_is_relevant("Event 'new' on sink #9")
        )

    def test_subscribe_argv_prefers_stdbuf_line_buffering(self) -> None:
        with patch(
            "tunes_player.platform.linux.volume_watch.shutil.which",
            return_value="/usr/bin/stdbuf",
        ):
            self.assertEqual(
                pactl_subscribe_argv("/usr/bin/pactl"),
                ["/usr/bin/stdbuf", "-oL", "/usr/bin/pactl", "subscribe"],
            )

    def test_subscribe_argv_falls_back_without_stdbuf(self) -> None:
        with patch(
            "tunes_player.platform.linux.volume_watch.shutil.which",
            return_value=None,
        ):
            self.assertEqual(
                pactl_subscribe_argv("/usr/bin/pactl"),
                ["/usr/bin/pactl", "subscribe"],
            )


class StackVolumeWatcherTests(unittest.TestCase):
    def test_poll_notifies_on_external_level_change(self) -> None:
        levels = iter([0.40, 0.40, 0.55])
        seen: list[float] = []

        def read_level() -> float:
            try:
                return next(levels)
            except StopIteration:
                return 0.55

        watcher = StackVolumeWatcher(
            should_watch=lambda: True,
            read_level=read_level,
            on_external=seen.append,
            watch_mode=lambda: "poll",
            poll_interval_sec=0.05,
            pactl_path=None,
        )
        watcher.start()
        try:
            deadline = time.monotonic() + 2.0
            while not seen and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(seen, [0.55])
        finally:
            watcher.stop()

    def test_delta_below_threshold_does_not_notify(self) -> None:
        levels = iter([0.50, 0.5005, 0.5004])
        seen: list[float] = []

        def read_level() -> float:
            try:
                return next(levels)
            except StopIteration:
                return 0.5004

        watcher = StackVolumeWatcher(
            should_watch=lambda: True,
            read_level=read_level,
            on_external=seen.append,
            watch_mode=lambda: "poll",
            poll_interval_sec=0.05,
            pactl_path=None,
        )
        watcher.start()
        try:
            time.sleep(0.25)
            self.assertEqual(seen, [])
        finally:
            watcher.stop()

    def test_should_watch_false_never_notifies(self) -> None:
        read_level = MagicMock(return_value=0.8)
        seen: list[float] = []
        watcher = StackVolumeWatcher(
            should_watch=lambda: False,
            read_level=read_level,
            on_external=seen.append,
            watch_mode=lambda: "poll",
            poll_interval_sec=0.05,
            pactl_path=None,
        )
        watcher.start()
        try:
            time.sleep(0.2)
            self.assertEqual(seen, [])
            read_level.assert_not_called()
        finally:
            watcher.stop()

    def test_note_applied_level_suppresses_echo(self) -> None:
        current = {"level": 0.3}
        seen: list[float] = []
        watcher = StackVolumeWatcher(
            should_watch=lambda: True,
            read_level=lambda: current["level"],
            on_external=seen.append,
            watch_mode=lambda: "poll",
            poll_interval_sec=0.05,
            pactl_path=None,
        )
        watcher.start()
        try:
            time.sleep(0.08)  # baseline at 0.3
            current["level"] = 0.7
            watcher.note_applied_level(0.7)
            time.sleep(0.2)
            self.assertEqual(seen, [])
            current["level"] = 0.2
            deadline = time.monotonic() + 2.0
            while not seen and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(seen, [0.2])
        finally:
            watcher.stop()


if __name__ == "__main__":
    unittest.main()
