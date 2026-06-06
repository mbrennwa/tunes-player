#!/usr/bin/env python3
"""Generate system-wide CPU load to reproduce audio stuttering (issue #29).

Tunes co-locates mpv with the USB xHCI IRQ CPU when possible. This script
stresses the other logical CPUs so playback and load generators do not share
that core.

Examples:
  # ~65% on CPUs 1..N-1, CPU 0 left for mpv (default)
  python3 scripts/cpu_load.py

  # Match reported repro (~60%+), run for 5 minutes
  python3 scripts/cpu_load.py --load 0.60 --duration 300

  # Stress every CPU including the playback core (old behaviour)
  python3 scripts/cpu_load.py --reserve-cpus ""
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import signal
import sys
import time


def _burn_cpu(load: float, stop: mp.Event) -> None:
    """Maintain *load* duty cycle (0.0–1.0) in this worker process."""
    load = max(0.01, min(1.0, load))
    window_sec = 0.05
    while not stop.is_set():
        deadline = time.perf_counter() + window_sec * load
        while time.perf_counter() < deadline and not stop.is_set():
            acc = 0
            for i in range(256):
                acc += i * i
            if acc < 0:  # noqa: B018 — unreachable guard for optimizers
                break
        remaining = window_sec * (1.0 - load)
        if remaining > 0 and not stop.is_set():
            time.sleep(remaining)


def _worker_entry(load: float, stop: mp.Event, cpu: int | None) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if cpu is not None and hasattr(os, "sched_setaffinity"):
        try:
            os.sched_setaffinity(0, {cpu})
        except OSError:
            pass
    _burn_cpu(load, stop)


def _cpu_count() -> int:
    count = os.cpu_count() or 1
    return max(1, count)


def _default_reserve_cpus() -> str:
    try:
        from tunes_player.platform.linux.usb_irq import xhci_irq_cpu

        cpu = xhci_irq_cpu()
        if cpu is not None:
            return str(cpu)
    except ImportError:
        pass
    return "0"


def _parse_cpu_set(text: str) -> set[int]:
    if not text.strip():
        return set()
    cpus: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        cpus.add(int(part))
    return cpus


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate system-wide CPU load to stress-test realtime audio playback "
            "(tunes-player issue #29)."
        ),
    )
    parser.add_argument(
        "--load",
        type=float,
        default=0.65,
        metavar="FRACTION",
        help=(
            "Target aggregate CPU duty cycle per worker (0.01–1.0). "
            "With one worker per stressed CPU, total system load is roughly "
            "load × 100%% × stressed_cpus / total_cpus (default: 0.65)."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=0,
        metavar="N",
        help="Worker processes (default: one per non-reserved CPU).",
    )
    parser.add_argument(
        "--reserve-cpus",
        type=str,
        default=None,
        metavar="LIST",
        help=(
            "Comma-separated CPU indices left idle for audio playback "
            "(default: USB xHCI IRQ CPU when detectable, else 0)."
        ),
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        metavar="SEC",
        help="Stop after SEC seconds (default: run until Ctrl+C).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not 0.0 < args.load <= 1.0:
        print("error: --load must be between 0 and 1", file=sys.stderr)
        return 2

    total_cpus = _cpu_count()
    reserve_text = args.reserve_cpus if args.reserve_cpus is not None else _default_reserve_cpus()
    reserved = _parse_cpu_set(reserve_text)
    stressed_cpus = [cpu for cpu in range(total_cpus) if cpu not in reserved]
    if not stressed_cpus:
        print("error: all CPUs are reserved; nothing to stress", file=sys.stderr)
        return 2

    workers = args.workers if args.workers > 0 else len(stressed_cpus)
    workers = min(workers, len(stressed_cpus))
    stop = mp.Event()
    processes: list[mp.Process] = []

    def _shutdown(*_args: object) -> None:
        stop.set()
        for proc in processes:
            proc.join(timeout=2.0)
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=1.0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    reserve_label = ",".join(str(cpu) for cpu in sorted(reserved)) or "(none)"
    stress_label = ",".join(str(cpu) for cpu in stressed_cpus[:workers])
    print(
        f"cpu_load: {workers} worker(s) at {args.load * 100:.0f}% duty cycle "
        f"on CPU(s) {stress_label}; reserved for playback: {reserve_label}",
        flush=True,
    )
    print("Play audio in Tunes (direct ALSA) while this runs. Press Ctrl+C to stop.", flush=True)

    for index in range(workers):
        cpu = stressed_cpus[index]
        proc = mp.Process(
            target=_worker_entry,
            args=(args.load, stop, cpu),
            daemon=True,
        )
        proc.start()
        processes.append(proc)

    try:
        if args.duration > 0:
            time.sleep(args.duration)
            _shutdown()
        else:
            while any(proc.is_alive() for proc in processes):
                time.sleep(0.25)
    except KeyboardInterrupt:
        _shutdown()

    print("cpu_load: stopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
