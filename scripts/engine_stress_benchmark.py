#!/usr/bin/env python3
"""Headless in-process mpv benchmark under CPU stress (#29 / #46).

Plays local staged tracks on USB direct ALSA via ``MpvEngine`` while
``cpu_load.py`` runs, logs phase markers to tunes-player.log, and prints
xrun/error metrics.

Example:
  python3 scripts/engine_stress_benchmark.py --load 0.80
  python3 scripts/engine_stress_benchmark.py --seconds 25 --load 0.80 --skip-stress
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tunes_player.core.config import ConfigManager
from tunes_player.core.logging_config import configure_logging
from tunes_player.core.playback.output_profile import PlaybackOutputProfile
from tunes_player.engines.factory import create_playback_engine
from tunes_player.core.playback.mpv_events import END_FILE_ERROR
from tunes_player.platform.linux.alsa_playback import effective_mpv_alsa_device
from tunes_player.platform.linux.alsa_xrun_monitor import AlsaXrunMonitor, parse_card_from_mpv_device

_LOG = logging.getLogger("tunes_player.benchmark")

_DEFAULT_TRACKS: tuple[tuple[str, str, PlaybackOutputProfile], ...] = (
    (
        "96k",
        "66611506165e7c3c0e93/01 San Andreas Fault.m4a",
        PlaybackOutputProfile(
            direct_alsa=True,
            use_exclusive=False,
            allow_resample=False,
            target_rate=96000,
            target_bit_depth=24,
            target_channels=2,
            audio_format="s32",
        ),
    ),
    (
        "44.1k",
        "551e972c214f240adb7d/01 33 RPM Soul.m4a",
        PlaybackOutputProfile(
            direct_alsa=True,
            use_exclusive=False,
            allow_resample=False,
            target_rate=44100,
            target_bit_depth=16,
            target_channels=2,
            audio_format="s16",
        ),
    ),
    (
        "44.1k-2",
        "31c65e6de7dd5f7e41cf/01 What Goes On.m4a",
        PlaybackOutputProfile(
            direct_alsa=True,
            use_exclusive=False,
            allow_resample=False,
            target_rate=44100,
            target_bit_depth=16,
            target_channels=2,
            audio_format="s16",
        ),
    ),
)

_ALSA_XRUN_RE = re.compile(
    r"ALSA PCM entered XRUN|ALSA xrun counter increased",
    re.IGNORECASE,
)
_END_FILE_RE = re.compile(r"end-file reason=(\d+|[a-z]+)")


@dataclass
class PhaseMetrics:
    alsa_xrun: int = 0
    end_file_errors: int = 0
    playback_errors: int = 0
    lines: list[str] = field(default_factory=list)


def _is_end_file_error(token: str) -> bool:
    if token == "error":
        return True
    if token.isdigit():
        return int(token) == END_FILE_ERROR
    return False


class _PhaseLogCounter(logging.Handler):
    """Count xrun and playback error log lines during one benchmark phase."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.metrics = PhaseMetrics()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            return
        self.metrics.lines.append(message)
        if "ALSA PCM entered XRUN" in message or "ALSA xrun counter increased" in message:
            self.metrics.alsa_xrun += 1
        match = _END_FILE_RE.search(message)
        if match is not None and _is_end_file_error(match.group(1)):
            self.metrics.end_file_errors += 1
        if record.name.startswith("tunes_player") and "playback_error" in message:
            self.metrics.playback_errors += 1


def _cache_dir() -> Path:
    return ConfigManager().data_dir / "playback-cache"


def _resolve_tracks(cache_dir: Path) -> list[tuple[str, Path, PlaybackOutputProfile]]:
    tracks: list[tuple[str, Path, PlaybackOutputProfile]] = []
    for label, rel, profile in _DEFAULT_TRACKS:
        path = cache_dir / rel
        if not path.is_file():
            raise FileNotFoundError(f"Missing staged track for benchmark: {path}")
        tracks.append((label, path, profile))
    return tracks


def _stop_stray_mpv() -> None:
    try:
        proc = subprocess.run(
            ["pgrep", "-x", "mpv"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.isdigit():
            continue
        pid = int(line)
        try:
            os.kill(pid, signal.SIGTERM)
            _LOG.info("Stopped stray mpv pid %d before benchmark", pid)
        except OSError:
            pass
    time.sleep(0.5)


def _start_cpu_load(
    load: float,
    duration_sec: float,
    *,
    stress_playback_cpu: bool,
) -> subprocess.Popen[bytes]:
    script = REPO_ROOT / "scripts" / "cpu_load.py"
    cmd = [
        sys.executable,
        str(script),
        "--load",
        str(load),
        "--duration",
        str(int(max(30, duration_sec + 30))),
    ]
    if stress_playback_cpu:
        cmd.extend(["--reserve-cpus", ""])
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _run_phase(
    *,
    phase: str,
    session: str,
    tracks: list[tuple[str, Path, PlaybackOutputProfile]],
    seconds_per_track: float,
    mpv_device: str,
    endpoint_id: str,
    card: int | None,
    counter: _PhaseLogCounter,
) -> PhaseMetrics:
    _LOG.info(
        "ENGINE_BENCHMARK session=%s phase=%s engine=inprocess start tracks=%d sec=%.0f",
        session,
        phase,
        len(tracks),
        seconds_per_track,
    )

    monitor = AlsaXrunMonitor()
    monitor.set_card(card)
    playback_errors = 0

    def on_event(event: str) -> None:
        nonlocal playback_errors
        if event == "playback_error":
            playback_errors += 1

    profile = tracks[0][2]
    engine = create_playback_engine(
        unity_gain=True,
        volume=1.0,
        audio_device=mpv_device,
        use_device_output=True,
        output_profile=profile,
        on_event=on_event,
        endpoint_id=endpoint_id,
    )

    try:
        for label, path, track_profile in tracks:
            _LOG.info(
                "ENGINE_BENCHMARK session=%s phase=%s track=%s path=%s",
                session,
                phase,
                label,
                path.name,
            )
            engine.load(str(path), output_profile=track_profile)
            deadline = time.monotonic() + seconds_per_track
            while time.monotonic() < deadline:
                monitor.poll(mpv_audio_device=mpv_device, endpoint_id=endpoint_id)
                time.sleep(0.15)
    finally:
        engine.quit()

    metrics = counter.metrics
    metrics.playback_errors = playback_errors
    _LOG.info(
        "ENGINE_BENCHMARK session=%s phase=%s engine=inprocess end "
        "alsa_xrun=%d end_file_error=%d playback_error=%d",
        session,
        phase,
        metrics.alsa_xrun,
        metrics.end_file_errors,
        metrics.playback_errors,
    )
    return metrics


def _parse_log_slice(log_path: Path, session: str) -> Counter[str]:
    """Cross-check in-process counts against the persistent log file."""
    counts: Counter[str] = Counter()
    in_phase = False
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return counts

    for line in text.splitlines():
        if f"ENGINE_BENCHMARK session={session}" not in line:
            continue
        if " engine=inprocess start" in line or " engine=subprocess start" in line:
            in_phase = True
        elif " engine=inprocess end " in line or " engine=subprocess end " in line:
            in_phase = False
            continue
        if not in_phase:
            continue
        if _ALSA_XRUN_RE.search(line):
            counts["alsa_xrun"] += 1
        match = _END_FILE_RE.search(line)
        if match is not None and _is_end_file_error(match.group(1)):
            counts["end_file_error"] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=20.0, help="Seconds per track")
    parser.add_argument("--load", type=float, default=0.80, help="cpu_load.py duty cycle")
    parser.add_argument(
        "--device",
        default="alsa/hw:1,0",
        help="mpv ALSA device (default: Holo USB hw:1,0)",
    )
    parser.add_argument(
        "--endpoint",
        default="alsa:hw:1:0",
        help="Tunes endpoint id for USB helpers",
    )
    parser.add_argument(
        "--skip-stress",
        action="store_true",
        help="Run phases without cpu_load (baseline)",
    )
    parser.add_argument(
        "--stress-playback-cpu",
        action="store_true",
        help="Stress every CPU including the xHCI/playback core (harsher)",
    )
    args = parser.parse_args()

    os.environ.setdefault("TUNES_LOG_LEVEL", "INFO")
    config = ConfigManager()
    log_path = configure_logging(config.state_dir, legacy_data_dir=config.data_dir)
    tracks = _resolve_tracks(_cache_dir())
    mpv_device = effective_mpv_alsa_device(args.device) or args.device
    card = parse_card_from_mpv_device(mpv_device)

    session = uuid.uuid4().hex[:8]
    _LOG.info(
        "ENGINE_BENCHMARK session=%s begin device=%s load=%.2f stress=%s playback_cpu_stressed=%s",
        session,
        mpv_device,
        args.load,
        not args.skip_stress,
        args.stress_playback_cpu,
    )

    _stop_stray_mpv()

    total_sec = len(tracks) * args.seconds + 10
    stress_proc: subprocess.Popen[bytes] | None = None
    if not args.skip_stress:
        stress_proc = _start_cpu_load(
            args.load,
            total_sec,
            stress_playback_cpu=args.stress_playback_cpu,
        )
        time.sleep(1.0)

    app_logger = logging.getLogger("tunes_player")
    counter = _PhaseLogCounter()
    app_logger.addHandler(counter)

    try:
        metrics = _run_phase(
            phase="A",
            session=session,
            tracks=tracks,
            seconds_per_track=args.seconds,
            mpv_device=mpv_device,
            endpoint_id=args.endpoint,
            card=card,
            counter=counter,
        )
    finally:
        app_logger.removeHandler(counter)
        if stress_proc is not None:
            stress_proc.terminate()
            try:
                stress_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                stress_proc.kill()

    log_counts = _parse_log_slice(log_path, session)

    print()
    print(f"Benchmark session {session}  log: {log_path}")
    print(f"Device: {mpv_device}  CPU stress: {'yes' if not args.skip_stress else 'no'}  load={args.load}")
    print(f"{args.seconds:.0f}s × {len(tracks)} tracks (in-process MpvEngine)\n")
    header = f"{'alsa_xrun':>10} {'end_file_err':>13} {'playback_err':>13}"
    print(header)
    print("-" * len(header))
    print(
        f"{metrics.alsa_xrun:>10} {metrics.end_file_errors:>13} "
        f"{metrics.playback_errors:>13}"
    )
    print("\nLog-file cross-check (same session markers):")
    print(
        f"  alsa_xrun={log_counts['alsa_xrun']} "
        f"end_file_error={log_counts['end_file_error']}"
    )
    print("\nGrep this session:")
    print(f"  grep 'ENGINE_BENCHMARK session={session}' {log_path}")
    print(
        f"  grep -E 'session={session}|XRUN|xrun|end-file' {log_path} | "
        f"grep -E 'ENGINE_BENCHMARK|XRUN|end-file'"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
