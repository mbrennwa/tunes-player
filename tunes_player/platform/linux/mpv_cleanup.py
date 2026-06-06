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
    if "/plughw:" in device:
        suffix = device.split("/plughw:", 1)[1]
        patterns.add(f"--audio-device=plughw:{suffix}")
        patterns.add(f"--audio-device=alsa/plughw:{suffix}")
    return patterns


def _pids_holding_pcm_device(device: str) -> set[int]:
    """Return PIDs with an open handle on the card's playback PCM node."""
    try:
        from tunes_player.platform.linux.alsa_xrun_monitor import parse_card_from_mpv_device
    except ImportError:
        return set()
    card = parse_card_from_mpv_device(device)
    if card is None:
        return set()
    node = f"/dev/snd/pcmC{card}D0p"
    try:
        result = subprocess.run(
            ["fuser", node],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return set()
    pids: set[int] = set()
    for part in (result.stdout + " " + result.stderr).split():
        if part.isdigit():
            pids.add(int(part))
    return pids


def _is_mpv_process(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as comm_file:
            return comm_file.read().strip() == "mpv"
    except OSError:
        return False


def _terminate_pids(pids: set[int], *, exclude_pid: int | None, label: str) -> None:
    if not pids:
        return
    for pid in sorted(pids):
        if exclude_pid is not None and pid == exclude_pid:
            continue
        _LOG.warning("Terminating mpv pid %d holding %s", pid, label)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue

    time.sleep(0.25)

    for pid in sorted(pids):
        if exclude_pid is not None and pid == exclude_pid:
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        _LOG.warning("Force-killing mpv pid %d still holding %s", pid, label)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue

    if pids - ({exclude_pid} if exclude_pid is not None else set()):
        time.sleep(0.15)


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

    for pid in _pids_holding_pcm_device(device):
        if _is_mpv_process(pid):
            targets.add(pid)

    _terminate_pids(targets, exclude_pid=exclude_pid, label=device)
