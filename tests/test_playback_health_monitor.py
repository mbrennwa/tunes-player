"""Tests for playback health monitor (#67)."""

from __future__ import annotations

import os
import threading
import unittest
import unittest.mock

from tunes_player.core.playback import health_monitor as hm


class PlaybackHealthFlagTests(unittest.TestCase):
    def test_flag_defaults_off(self) -> None:
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TUNES_PLAYBACK_HEALTH_LOG", None)
            self.assertFalse(hm.playback_health_log_enabled())

    def test_flag_accepts_truthy(self) -> None:
        for value in ("1", "yes", "TRUE"):
            with self.subTest(value=value):
                with unittest.mock.patch.dict(
                    os.environ, {"TUNES_PLAYBACK_HEALTH_LOG": value}
                ):
                    self.assertTrue(hm.playback_health_log_enabled())


class PactlParseTests(unittest.TestCase):
    def test_parse_pactl_sinks(self) -> None:
        text = """
Sink #0
\tState: SUSPENDED
\tName: alsa_output.pci
\tMute: no

Sink #1
\tState: RUNNING
\tName: softvol
\tMute: yes
"""
        sinks = hm.parse_pactl_sinks(text)
        self.assertEqual(sinks["alsa_output.pci"], ("SUSPENDED", False))
        self.assertEqual(sinks["softvol"], ("RUNNING", True))

    def test_parse_sink_inputs_detects_mpv(self) -> None:
        text = """
Sink Input #12
\tSink: softvol
\tapplication.name = "mpv"
"""
        self.assertTrue(
            hm.parse_pactl_sink_inputs_for_mpv(text, sink_name="softvol")
        )
        self.assertFalse(
            hm.parse_pactl_sink_inputs_for_mpv(text, sink_name="other")
        )

    def test_parse_wpctl_muted(self) -> None:
        self.assertTrue(hm.parse_wpctl_muted("Volume: 0.50 [MUTED]"))
        self.assertFalse(hm.parse_wpctl_muted("Volume: 0.50"))


class EvaluateIssuesTests(unittest.TestCase):
    def _sample(self, **kwargs: object) -> hm.PlaybackHealthSample:
        base = dict(
            intended_playing=True,
            engine_playing=True,
            time_pos_sec=10.0,
            sampled_at=100.0,
            ao="pulse",
            audio_device="pulse/softvol",
            core_idle=False,
            paused_for_cache=False,
            mute=False,
            mpv_audio_device="pulse/softvol",
        )
        base.update(kwargs)
        return hm.PlaybackHealthSample(**base)  # type: ignore[arg-type]

    def test_time_pos_stall(self) -> None:
        previous = self._sample(time_pos_sec=10.0, sampled_at=100.0)
        current = self._sample(time_pos_sec=10.01, sampled_at=101.0)
        codes = {i.code for i in hm.evaluate_engine_issues(current, previous)}
        self.assertIn("time_pos_stalled", codes)

    def test_seek_backward_not_stall(self) -> None:
        previous = self._sample(time_pos_sec=40.0, sampled_at=100.0)
        current = self._sample(time_pos_sec=5.0, sampled_at=101.0)
        codes = {i.code for i in hm.evaluate_engine_issues(current, previous)}
        self.assertNotIn("time_pos_stalled", codes)

    def test_core_idle_and_cache_pause(self) -> None:
        current = self._sample(core_idle=True, paused_for_cache=True)
        codes = {i.code for i in hm.evaluate_engine_issues(current, None)}
        self.assertEqual(codes, {"core_idle", "paused_for_cache"})

    def test_sink_not_running(self) -> None:
        sample = self._sample()
        sink = hm.SinkHealth(
            backend="pactl",
            state="SUSPENDED",
            muted=False,
            sink_name="softvol",
            has_playing_input=True,
        )
        codes = {i.code for i in hm.evaluate_sink_issues(sample, sink)}
        self.assertIn("sink_not_running", codes)

    def test_direct_alsa_skips_sink_checks(self) -> None:
        sample = self._sample(ao="alsa", audio_device="alsa/hw:1,0")
        sink = hm.SinkHealth(backend="pactl", state="SUSPENDED", muted=True)
        self.assertEqual(hm.evaluate_sink_issues(sample, sink), [])

    def test_pulse_device_name(self) -> None:
        self.assertEqual(
            hm.pulse_sink_name_from_mpv_device("pulse/my.sink"),
            "my.sink",
        )
        self.assertIsNone(hm.pulse_sink_name_from_mpv_device("alsa/hw:0,0"))


class PlaybackHealthMonitorTests(unittest.TestCase):
    def test_sustained_issue_logs_once(self) -> None:
        clock = {"t": 0.0}

        def now() -> float:
            return clock["t"]

        sink = hm.SinkHealth(
            backend="pactl",
            state="SUSPENDED",
            muted=False,
            sink_name="softvol",
            has_playing_input=True,
        )
        monitor = hm.PlaybackHealthMonitor(
            interval_sec=0.2,
            sustain_sec=1.0,
            sink_probe=lambda **_kwargs: sink,
            clock=now,
        )
        sample = hm.PlaybackHealthSample(
            intended_playing=True,
            engine_playing=True,
            time_pos_sec=1.0,
            sampled_at=0.0,
            ao="pulse",
            mpv_audio_device="pulse/softvol",
        )
        monitor.publish_sample(sample)

        with self.assertNoLogs(
            "tunes_player.core.playback.health_monitor", level="WARNING"
        ):
            clock["t"] = 0.5
            self.assertEqual(monitor.poll_once(), [])

        with self.assertLogs(
            "tunes_player.core.playback.health_monitor", level="WARNING"
        ) as logs:
            clock["t"] = 1.5
            sustained = monitor.poll_once()
            monitor.poll_once()

        self.assertTrue(any(i.code == "sink_not_running" for i in sustained))
        self.assertEqual(
            sum("sink state=SUSPENDED" in r.getMessage() for r in logs.records),
            1,
        )

    def test_monitor_thread_runs_and_stops(self) -> None:
        probed = threading.Event()

        def sink_probe(**_kwargs: object) -> hm.SinkHealth:
            probed.set()
            return hm.SinkHealth(backend="none")

        monitor = hm.PlaybackHealthMonitor(
            interval_sec=0.05,
            sustain_sec=0.0,
            sink_probe=sink_probe,
        )
        monitor.publish_sample(
            hm.PlaybackHealthSample(
                intended_playing=True,
                engine_playing=True,
                time_pos_sec=0.0,
                sampled_at=0.0,
            )
        )
        monitor.start()
        self.assertTrue(monitor.is_alive)
        self.assertTrue(probed.wait(timeout=2.0))
        monitor.stop()
        self.assertFalse(monitor.is_alive)

    def test_create_monitor_respects_flag(self) -> None:
        with unittest.mock.patch.dict(
            os.environ, {"TUNES_PLAYBACK_HEALTH_LOG": "0"}
        ):
            self.assertIsNone(hm.create_playback_health_monitor())

        with unittest.mock.patch.dict(
            os.environ, {"TUNES_PLAYBACK_HEALTH_LOG": "1"}
        ), unittest.mock.patch.object(
            hm.PlaybackHealthMonitor, "start"
        ) as start:
            monitor = hm.create_playback_health_monitor()
            self.assertIsNotNone(monitor)
            start.assert_called_once()
            assert monitor is not None
            monitor.stop()

    def test_sample_from_mpv_properties(self) -> None:
        props = {
            "ao": "pulse",
            "audio-device": "pulse/x",
            "core-idle": True,
            "paused-for-cache": "no",
            "mute": "yes",
        }
        sample = hm.sample_from_mpv_properties(
            props.__getitem__,
            intended_playing=True,
            engine_playing=True,
            time_pos_sec=3.5,
            sampled_at=9.0,
            endpoint_id="pw:x",
            mpv_audio_device="pulse/x",
        )
        self.assertEqual(sample.ao, "pulse")
        self.assertTrue(sample.core_idle)
        self.assertFalse(sample.paused_for_cache)
        self.assertTrue(sample.mute)
        self.assertEqual(sample.endpoint_id, "pw:x")


class ProbeSinkHealthTests(unittest.TestCase):
    def test_probe_uses_pactl_when_available(self) -> None:
        sinks = """
Sink #0
\tState: IDLE
\tName: demo
\tMute: no
"""
        inputs = """
Sink Input #1
\tSink: demo
\tapplication.name = "mpv Media Player"
"""

        def fake_which(name: str) -> str | None:
            return "/usr/bin/" + name if name in ("pactl",) else None

        def fake_run(args: list[str], **_kwargs: object) -> str | None:
            if args[:3] == ["pactl", "list", "sinks"]:
                return sinks
            if args[:2] == ["pactl", "get-default-sink"]:
                return "demo\n"
            if args[:3] == ["pactl", "list", "sink-inputs"]:
                return inputs
            return None

        with unittest.mock.patch.object(hm.shutil, "which", side_effect=fake_which):
            with unittest.mock.patch.object(hm, "_run_cmd", side_effect=fake_run):
                health = hm.probe_sink_health()
        self.assertEqual(health.backend, "pactl")
        self.assertEqual(health.state, "IDLE")
        self.assertEqual(health.sink_name, "demo")
        self.assertTrue(health.has_playing_input)


if __name__ == "__main__":
    unittest.main()
