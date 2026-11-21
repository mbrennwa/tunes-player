"""MpdEngine event mapping against a fake MPD server (#60)."""

from __future__ import annotations

import threading
import time
import unittest
from collections.abc import Callable

from tests.fake_mpd import BUSY_MESSAGE, FakeMpdServer
from tunes_player.engines.mpd import MpdEngine


def wait_for(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class _Events:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.items: list[str] = []

    def __call__(self, event: str) -> None:
        with self._lock:
            self.items.append(event)

    def count(self, name: str) -> int:
        with self._lock:
            return self.items.count(name)

    def clear(self) -> None:
        with self._lock:
            self.items.clear()


class MpdEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = FakeMpdServer()
        self.addCleanup(self.server.close)
        self.events = _Events()
        self.engine = MpdEngine(
            self.server.host,
            self.server.port,
            name="test",
            on_event=self.events,
            poll_playing_sec=0.02,
            poll_idle_sec=0.02,
        )
        self.addCleanup(self.engine.quit)

    def test_load_starts_playback_and_emits_events(self) -> None:
        self.engine.load("http://host/f/abc/track.flac")
        self.assertEqual(self.server.queue, ["http://host/f/abc/track.flac"])
        self.assertEqual(self.server.state, "play")
        self.assertTrue(self.engine.is_playing())
        for name in ("track_started", "duration_changed", "playing_changed"):
            self.assertGreaterEqual(self.events.count(name), 1, name)
        self.assertTrue(wait_for(lambda: self.engine.get_duration() == 200.0))

    def test_load_replaces_the_queue(self) -> None:
        self.engine.load("http://host/a.flac")
        self.engine.load("http://host/b.flac")
        self.assertEqual(self.server.queue, ["http://host/b.flac"])

    def test_load_with_start_offset_seeks(self) -> None:
        self.engine.load("http://host/a.flac", start_sec=42.5)
        self.assertEqual(self.server.commands_named("seek")[-1], 'seek "0" "42.500"')
        self.assertAlmostEqual(self.engine.get_position(), 42.5, delta=1.0)

    def test_failed_add_raises_runtime_error(self) -> None:
        with self.assertRaises(RuntimeError):
            self.engine.load("/x/missing.flac")
        self.assertFalse(self.engine.is_playing())

    def test_pause_and_resume(self) -> None:
        self.engine.load("http://host/a.flac")
        self.engine.pause()
        self.assertEqual(self.server.state, "pause")
        self.assertFalse(self.engine.is_playing())
        self.engine.play()
        self.assertEqual(self.server.state, "play")
        self.assertTrue(self.engine.is_playing())

    def test_external_pause_is_noticed(self) -> None:
        self.engine.load("http://host/a.flac")
        self.server.state = "pause"
        self.assertTrue(wait_for(lambda: not self.engine.is_playing()))

    def test_seek_sends_seekcur(self) -> None:
        self.engine.load("http://host/a.flac")
        self.assertTrue(wait_for(lambda: self.engine.get_duration() is not None))
        self.engine.seek(30.0)
        self.assertTrue(self.server.commands_named("seekcur"))
        self.assertAlmostEqual(self.server.elapsed, 30.0, delta=0.5)

    def test_seek_is_kept_away_from_the_end(self) -> None:
        self.engine.load("http://host/a.flac")
        self.assertTrue(wait_for(lambda: self.engine.get_duration() is not None))
        self.engine.seek(10_000.0)
        self.assertLessEqual(self.server.elapsed, 199.0)

    def test_natural_end_emits_track_eof(self) -> None:
        self.engine.load("http://host/a.flac")
        self.assertTrue(wait_for(lambda: self.engine.get_duration() is not None))
        self.server.finish_track()
        self.assertTrue(wait_for(lambda: self.events.count("track_eof") == 1))
        time.sleep(0.15)
        self.assertEqual(self.events.count("track_eof"), 1)
        self.assertFalse(self.engine.is_playing())

    def test_explicit_stop_is_not_a_track_end(self) -> None:
        self.engine.load("http://host/a.flac")
        self.assertTrue(wait_for(lambda: self.engine.get_duration() is not None))
        self.engine.stop()
        time.sleep(0.2)
        self.assertEqual(self.events.count("track_eof"), 0)
        self.assertFalse(self.engine.is_playing())

    def test_position_never_reaches_the_end_while_playing(self) -> None:
        self.engine.load("http://host/a.flac")
        self.assertTrue(wait_for(lambda: self.engine.get_duration() is not None))
        self.server.elapsed = 199.9
        time.sleep(0.2)
        self.assertLessEqual(self.engine.get_position(), 200.0 - 1.5 + 1e-6)

    def test_busy_output_reports_error(self) -> None:
        self.server.busy = True
        self.engine.load("http://host/a.flac")
        self.assertTrue(wait_for(lambda: self.events.count("playback_error") >= 1))
        self.assertIn(BUSY_MESSAGE, self.engine.last_error or "")
        self.assertFalse(self.engine.is_playing())
        # One error report, not one per poll.
        time.sleep(0.2)
        self.assertEqual(self.events.count("playback_error"), 1)

    def test_play_again_after_busy_error_works_once_device_is_free(self) -> None:
        self.server.busy = True
        self.engine.load("http://host/a.flac")
        self.assertTrue(wait_for(lambda: self.events.count("playback_error") >= 1))
        self.server.busy = False
        self.engine.play()
        self.assertTrue(wait_for(lambda: self.server.state == "play"))
        self.assertTrue(wait_for(self.engine.is_playing))

    def test_unreachable_server_raises_on_construction(self) -> None:
        port = self.server.port
        self.server.close()
        with self.assertRaises(RuntimeError) as ctx:
            MpdEngine("127.0.0.1", port, name="gone")
        self.assertIn("gone", str(ctx.exception))

    def test_volume_and_bit_perfect_are_noops(self) -> None:
        self.engine.set_volume(0.3)
        self.engine.set_bit_perfect(True)
        self.engine.set_output_profile(None)
        self.assertEqual(self.server.commands_named("setvol"), [])

    def test_quit_stops_mpd(self) -> None:
        self.engine.load("http://host/a.flac")
        self.engine.quit()
        self.assertEqual(self.server.state, "stop")


if __name__ == "__main__":
    unittest.main()
