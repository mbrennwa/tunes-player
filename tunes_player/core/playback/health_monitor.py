"""Playback health diagnostics for silent-audio / stalled-progress issues (#67).

A daemon thread runs by default and compares recent engine samples (published
from the GTK/owner poll path) against PipeWire/Pulse sink state and optional
ALSA PCM health, logging sustained mismatches. Disable with
``TUNES_PLAYBACK_HEALTH_LOG=0``.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tunes_player.platform.linux.alsa_xrun_monitor import AlsaXrunMonitor

log = logging.getLogger(__name__)

_ENV_FLAG = "TUNES_PLAYBACK_HEALTH_LOG"
_DEFAULT_INTERVAL_SEC = 1.0
_DEFAULT_SUSTAIN_SEC = 1.5
_DEFAULT_HEARTBEAT_SEC = 10.0
_MIN_TIME_POS_ADVANCE_RATIO = 0.25

_PACTL_STATE = re.compile(r"^\s*State:\s*(\S+)", re.MULTILINE)
_PACTL_MUTE = re.compile(r"^\s*Mute:\s*(\S+)", re.MULTILINE)
_PACTL_NAME = re.compile(r"^\s*Name:\s*(\S+)", re.MULTILINE)
_PACTL_SINK = re.compile(r"^\s*Sink:\s*(\S+)", re.MULTILINE)
_PACTL_APP_NAME = re.compile(
    r'^\s*application\.name\s*=\s*"([^"]*)"',
    re.MULTILINE | re.IGNORECASE,
)
_WPCTL_MUTED = re.compile(r"\[MUTED\]", re.IGNORECASE)


def playback_health_log_enabled() -> bool:
    """Health monitor runs by default; set TUNES_PLAYBACK_HEALTH_LOG=0 to disable."""
    raw = os.environ.get(_ENV_FLAG)
    if raw is None or raw.strip() == "":
        return True
    return raw.lower() not in ("0", "no", "false", "off")


@dataclass(frozen=True, slots=True)
class PlaybackHealthSample:
    """Engine/UI snapshot published from the owner/GTK poll thread."""

    intended_playing: bool
    engine_playing: bool
    time_pos_sec: float
    sampled_at: float
    ao: str | None = None
    audio_device: str | None = None
    core_idle: bool | None = None
    paused_for_cache: bool | None = None
    mute: bool | None = None
    endpoint_id: str | None = None
    mpv_audio_device: str | None = None


@dataclass(frozen=True, slots=True)
class SinkHealth:
    backend: str
    state: str | None = None
    muted: bool | None = None
    sink_name: str | None = None
    has_playing_input: bool | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class HealthIssue:
    code: str
    message: str


def _coerce_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("yes", "true", "1", "on"):
        return True
    if text in ("no", "false", "0", "off"):
        return False
    return None


def _coerce_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def sample_from_mpv_properties(
    get_property: Callable[[str], object],
    *,
    intended_playing: bool,
    engine_playing: bool,
    time_pos_sec: float,
    sampled_at: float | None = None,
    endpoint_id: str | None = None,
    mpv_audio_device: str | None = None,
) -> PlaybackHealthSample:
    """Build a sample from mpv property reads (call from engine owner thread)."""
    return PlaybackHealthSample(
        intended_playing=intended_playing,
        engine_playing=engine_playing,
        time_pos_sec=max(0.0, float(time_pos_sec)),
        sampled_at=time.monotonic() if sampled_at is None else sampled_at,
        ao=_coerce_text(get_property("ao")),
        audio_device=_coerce_text(get_property("audio-device")),
        core_idle=_coerce_bool(get_property("core-idle")),
        paused_for_cache=_coerce_bool(get_property("paused-for-cache")),
        mute=_coerce_bool(get_property("mute")),
        endpoint_id=endpoint_id,
        mpv_audio_device=mpv_audio_device or _coerce_text(get_property("audio-device")),
    )


def parse_pactl_sink_block(text: str) -> tuple[str | None, str | None, bool | None]:
    """Return (name, state, muted) from one ``pactl list sinks`` block."""
    name_match = _PACTL_NAME.search(text)
    state_match = _PACTL_STATE.search(text)
    mute_match = _PACTL_MUTE.search(text)
    name = name_match.group(1) if name_match else None
    state = state_match.group(1) if state_match else None
    muted = None
    if mute_match is not None:
        muted = mute_match.group(1).lower() in ("yes", "true", "1")
    return name, state, muted


def parse_pactl_sinks(text: str) -> dict[str, tuple[str | None, bool | None]]:
    """Map sink name → (state, muted)."""
    sinks: dict[str, tuple[str | None, bool | None]] = {}
    for block in re.split(r"\n(?=Sink #)", text):
        name, state, muted = parse_pactl_sink_block(block)
        if name:
            sinks[name] = (state, muted)
    return sinks


def parse_pactl_sink_inputs_for_mpv(text: str, *, sink_name: str | None) -> bool | None:
    """True if an mpv-related sink-input exists (optionally on ``sink_name``)."""
    found_any = False
    matched_sink = False
    for block in re.split(r"\n(?=Sink Input #)", text):
        app_match = _PACTL_APP_NAME.search(block)
        if app_match is None:
            continue
        app = app_match.group(1).lower()
        if "mpv" not in app and "tunes" not in app:
            continue
        found_any = True
        if sink_name is None:
            matched_sink = True
            continue
        sink_match = _PACTL_SINK.search(block)
        if sink_match is not None and sink_match.group(1) == sink_name:
            matched_sink = True
    if not found_any:
        return False
    return matched_sink


def parse_wpctl_muted(volume_text: str) -> bool:
    return _WPCTL_MUTED.search(volume_text) is not None


def _run_cmd(args: list[str], *, timeout: float = 2.0) -> str | None:
    try:
        result = subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return result.stdout


def probe_sink_health(
    *,
    preferred_sink_name: str | None = None,
    wpctl_target: str | None = None,
) -> SinkHealth:
    """Probe Pulse/PipeWire sink state via pactl, with wpctl mute as fallback."""
    if shutil.which("pactl") is not None:
        sinks_out = _run_cmd(["pactl", "list", "sinks"])
        if sinks_out is not None:
            sinks = parse_pactl_sinks(sinks_out)
            default_out = _run_cmd(["pactl", "get-default-sink"])
            default_name = default_out.strip() if default_out else None
            sink_name = preferred_sink_name or default_name
            if sink_name is None and sinks:
                sink_name = next(iter(sinks))
            state: str | None = None
            muted: bool | None = None
            if sink_name is not None and sink_name in sinks:
                state, muted = sinks[sink_name]
            inputs_out = _run_cmd(["pactl", "list", "sink-inputs"])
            has_input: bool | None = None
            if inputs_out is not None:
                has_input = parse_pactl_sink_inputs_for_mpv(
                    inputs_out, sink_name=sink_name
                )
            return SinkHealth(
                backend="pactl",
                state=state,
                muted=muted,
                sink_name=sink_name,
                has_playing_input=has_input,
            )

    if shutil.which("wpctl") is not None:
        target = wpctl_target or "@DEFAULT_AUDIO_SINK@"
        volume_out = _run_cmd(["wpctl", "get-volume", target])
        if volume_out is not None:
            return SinkHealth(
                backend="wpctl",
                muted=parse_wpctl_muted(volume_out),
                sink_name=preferred_sink_name,
                detail=volume_out.strip(),
            )

    return SinkHealth(backend="none", detail="no pactl/wpctl")


def evaluate_engine_issues(
    current: PlaybackHealthSample,
    previous: PlaybackHealthSample | None,
) -> list[HealthIssue]:
    """Return engine-side issues for one sample (no external sink data)."""
    if not current.intended_playing or not current.engine_playing:
        return []

    issues: list[HealthIssue] = []
    if current.core_idle is True:
        issues.append(
            HealthIssue("core_idle", "mpv core-idle while intended playing")
        )
    if current.paused_for_cache is True:
        issues.append(
            HealthIssue(
                "paused_for_cache",
                "mpv paused-for-cache while intended playing",
            )
        )
    if current.mute is True:
        issues.append(HealthIssue("mpv_mute", "mpv mute=true while intended playing"))

    if previous is not None and previous.intended_playing and previous.engine_playing:
        elapsed = current.sampled_at - previous.sampled_at
        if elapsed >= 0.4:
            delta = current.time_pos_sec - previous.time_pos_sec
            if delta < -0.5:
                # Seek / track change — not a stall.
                pass
            elif delta < elapsed * _MIN_TIME_POS_ADVANCE_RATIO:
                issues.append(
                    HealthIssue(
                        "time_pos_stalled",
                        (
                            f"time-pos stalled "
                            f"(delta={delta:.3f}s over {elapsed:.3f}s)"
                        ),
                    )
                )
    return issues


def evaluate_sink_issues(
    sample: PlaybackHealthSample,
    sink: SinkHealth,
) -> list[HealthIssue]:
    """Return sink-side issues when playback is intended."""
    if not sample.intended_playing or not sample.engine_playing:
        return []
    # Direct ALSA does not use a PipeWire/Pulse sink path.
    ao = (sample.ao or "").lower()
    device = (sample.audio_device or sample.mpv_audio_device or "").lower()
    if ao == "alsa" or device.startswith("alsa/"):
        return []

    issues: list[HealthIssue] = []
    if sink.backend == "none":
        return issues
    if sink.state is not None and sink.state.upper() != "RUNNING":
        issues.append(
            HealthIssue(
                "sink_not_running",
                f"sink state={sink.state} (expected RUNNING) name={sink.sink_name}",
            )
        )
    if sink.muted is True:
        issues.append(
            HealthIssue(
                "sink_muted",
                f"sink muted via {sink.backend} name={sink.sink_name}",
            )
        )
    if sink.has_playing_input is False:
        issues.append(
            HealthIssue(
                "missing_sink_input",
                "no mpv/tunes sink-input while intended playing",
            )
        )
    return issues


def pulse_sink_name_from_mpv_device(device: str | None) -> str | None:
    """Extract Pulse/PipeWire sink name from mpv ``pulse/<name>`` device."""
    if not device:
        return None
    if device.startswith("pulse/"):
        name = device[len("pulse/") :].strip()
        return name or None
    return None


class PlaybackHealthMonitor:
    """Daemon thread that logs sustained playback health problems."""

    def __init__(
        self,
        *,
        interval_sec: float = _DEFAULT_INTERVAL_SEC,
        sustain_sec: float = _DEFAULT_SUSTAIN_SEC,
        heartbeat_sec: float = _DEFAULT_HEARTBEAT_SEC,
        sink_probe: Callable[..., SinkHealth] | None = None,
        alsa_monitor: AlsaXrunMonitor | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._interval_sec = max(0.2, float(interval_sec))
        self._sustain_sec = max(0.0, float(sustain_sec))
        self._heartbeat_sec = max(0.0, float(heartbeat_sec))
        self._sink_probe = sink_probe or probe_sink_health
        self._alsa_monitor = alsa_monitor
        self._clock = clock
        self._lock = threading.Lock()
        self._latest: PlaybackHealthSample | None = None
        self._previous: PlaybackHealthSample | None = None
        self._issue_since: dict[str, float] = {}
        self._logged_codes: set[str] = set()
        self._last_heartbeat_at: float | None = None
        self._ticks = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="tunes-playback-health",
            daemon=True,
        )
        self._thread.start()
        log.info(
            "Playback health monitor started "
            "(interval=%.1fs sustain=%.1fs heartbeat=%.0fs; disable with %s=0)",
            self._interval_sec,
            self._sustain_sec,
            self._heartbeat_sec,
            _ENV_FLAG,
        )

    def stop(self, *, join_timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=join_timeout)
        self._thread = None
        with self._lock:
            self._latest = None
            self._previous = None
            self._issue_since.clear()
            self._logged_codes.clear()
            self._last_heartbeat_at = None
            self._ticks = 0
        if self._alsa_monitor is not None:
            self._alsa_monitor.reset()

    def publish_sample(self, sample: PlaybackHealthSample) -> None:
        with self._lock:
            self._latest = sample

    def _maybe_heartbeat(self, sample: PlaybackHealthSample | None) -> None:
        if self._heartbeat_sec <= 0:
            return
        now = self._clock()
        last = self._last_heartbeat_at
        if last is not None and now - last < self._heartbeat_sec:
            return
        self._last_heartbeat_at = now
        if sample is None:
            log.info(
                "playback health monitor alive (ticks=%d, no sample yet)",
                self._ticks,
            )
            return
        age = max(0.0, now - sample.sampled_at)
        active = sorted(self._issue_since)
        log.info(
            "playback health monitor alive "
            "(ticks=%d intended_playing=%s engine_playing=%s "
            "time-pos=%.3f sample_age=%.1fs ao=%s audio-device=%s "
            "active_issues=%s)",
            self._ticks,
            sample.intended_playing,
            sample.engine_playing,
            sample.time_pos_sec,
            age,
            sample.ao,
            sample.audio_device or sample.mpv_audio_device,
            ",".join(active) if active else "-",
        )

    def poll_once(self) -> list[HealthIssue]:
        """Run one evaluation cycle (used by the thread and by tests)."""
        with self._lock:
            current = self._latest
            previous = self._previous
            if current is not None:
                self._previous = current
            self._ticks += 1

        self._maybe_heartbeat(current)

        if current is None:
            return []

        if not current.intended_playing:
            with self._lock:
                self._issue_since.clear()
                self._logged_codes.clear()
            return []

        issues = evaluate_engine_issues(current, previous)
        preferred = pulse_sink_name_from_mpv_device(
            current.mpv_audio_device or current.audio_device
        )
        try:
            sink = self._sink_probe(preferred_sink_name=preferred)
        except Exception as exc:  # noqa: BLE001 — diagnostics must not crash
            log.debug("Sink health probe failed: %s", exc)
            sink = SinkHealth(backend="error", detail=str(exc))
        issues.extend(evaluate_sink_issues(current, sink))

        if self._alsa_monitor is not None:
            try:
                ao = (current.ao or "").lower()
                device = (
                    current.mpv_audio_device or current.audio_device or ""
                ).lower()
                endpoint = (current.endpoint_id or "").lower()
                expect_feeding = current.engine_playing and (
                    ao == "alsa"
                    or device.startswith("alsa/")
                    or endpoint.startswith("alsa:")
                )
                feed_issues = self._alsa_monitor.poll(
                    mpv_audio_device=current.mpv_audio_device or current.audio_device,
                    endpoint_id=current.endpoint_id,
                    expect_feeding=expect_feeding,
                )
                for feed in feed_issues:
                    issues.append(HealthIssue(code=feed.code, message=feed.message))
            except Exception as exc:  # noqa: BLE001
                log.debug("ALSA feed/xrun poll failed: %s", exc)

        now = self._clock()
        sustained: list[HealthIssue] = []
        active_codes = {issue.code for issue in issues}
        with self._lock:
            for code in list(self._issue_since):
                if code not in active_codes:
                    self._issue_since.pop(code, None)
                    self._logged_codes.discard(code)
            for issue in issues:
                started = self._issue_since.get(issue.code)
                if started is None:
                    self._issue_since[issue.code] = now
                    started = now
                if now - started >= self._sustain_sec:
                    sustained.append(issue)
                    if issue.code not in self._logged_codes:
                        self._logged_codes.add(issue.code)
                        log.warning(
                            "playback health: %s | ao=%s audio-device=%s "
                            "time-pos=%.3f core-idle=%s paused-for-cache=%s "
                            "mute=%s sink=%s",
                            issue.message,
                            current.ao,
                            current.audio_device or current.mpv_audio_device,
                            current.time_pos_sec,
                            current.core_idle,
                            current.paused_for_cache,
                            current.mute,
                            sink.state or sink.detail or sink.backend,
                        )
        return sustained

    def _run(self) -> None:
        while not self._stop.wait(self._interval_sec):
            try:
                self.poll_once()
            except Exception:
                log.exception("Playback health monitor tick failed")


def create_playback_health_monitor() -> PlaybackHealthMonitor | None:
    """Return a started monitor unless explicitly disabled via env."""
    if not playback_health_log_enabled():
        log.info(
            "Playback health monitor disabled (%s=%s)",
            _ENV_FLAG,
            os.environ.get(_ENV_FLAG),
        )
        return None
    alsa_monitor = None
    try:
        from tunes_player.platform.linux.alsa_xrun_monitor import AlsaXrunMonitor

        alsa_monitor = AlsaXrunMonitor()
    except Exception:  # noqa: BLE001
        log.debug("ALSA xrun monitor unavailable", exc_info=True)
    monitor = PlaybackHealthMonitor(alsa_monitor=alsa_monitor)
    monitor.start()
    return monitor
