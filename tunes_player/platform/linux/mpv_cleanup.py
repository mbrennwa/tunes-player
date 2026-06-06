"""Terminate orphan mpv processes that block ALSA devices."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time

_LOG = logging.getLogger(__name__)


def _device_pgrep_patterns(device: str) -> set[str]:
    patterns = {f"--audio-device={device}"}
    if device.startswith("alsa/"):
        patterns.add(f"--audio-device={device[5:]}")
    if "/hw:" in device:
        suffix = device.split("/hw:", 1)[1]
        patterns.add(f"--audio-device=hw:{suffix}")
        patterns.add(f"--audio-device=alsa/hw:{suffix}")
    return patterns


def terminate_mpv_using_audio_device(
    device: str | None,
    *,
    exclude_pid: int | None = None,
) -> None:
    """Stop other mpv processes that hold *device* (e.g. exclusive test instances)."""
    if not device:
        return

    targets: set[int] = set()
    for pattern in _device_pgrep_patterns(device):
        try:
            result = subprocess.run(
                ["pgrep", "-f", pattern],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            continue
        for line in result.stdout.splitlines():
            text = line.strip()
            if text.isdigit():
                targets.add(int(text))

    if not targets:
        return

    for pid in sorted(targets):
        if exclude_pid is not None and pid == exclude_pid:
            continue
        _LOG.warning("Terminating mpv pid %d holding %s", pid, device)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue

    time.sleep(0.25)

    for pid in sorted(targets):
        if exclude_pid is not None and pid == exclude_pid:
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        _LOG.warning("Force-killing mpv pid %d still holding %s", pid, device)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue

    if targets - ({exclude_pid} if exclude_pid is not None else set()):
        time.sleep(0.15)
