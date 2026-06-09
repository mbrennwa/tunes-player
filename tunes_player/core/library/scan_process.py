"""Helpers for the library scan subprocess lifecycle."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from pathlib import Path

_LOG = logging.getLogger(__name__)


def _cmdline_for_pid(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8",
            errors="replace",
        )
    except OSError:
        return ""


def _pid_opens_path(pid: int, resolved_path: str) -> bool:
    fd_dir = Path(f"/proc/{pid}/fd")
    try:
        for entry in fd_dir.iterdir():
            try:
                target = os.readlink(entry)
            except OSError:
                continue
            if target == resolved_path:
                return True
    except OSError:
        return False
    return False


def _scan_worker_pids(*, db_path: Path) -> set[int]:
    resolved_db = str(db_path.resolve())
    candidates: set[int] = set()
    for needle in (resolved_db, "tunes-library-scan", "run_library_scan", "spawn_main"):
        try:
            result = subprocess.run(
                ["pgrep", "-f", needle],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            continue
        for line in result.stdout.splitlines():
            text = line.strip()
            if text.isdigit():
                candidates.add(int(text))

    current_pid = os.getpid()
    workers: set[int] = set()
    for pid in candidates:
        if pid == current_pid:
            continue
        cmdline = _cmdline_for_pid(pid)
        if (
            "run_library_scan" in cmdline
            or "tunes-library-scan" in cmdline
            or resolved_db in cmdline
            or (
                "spawn_main" in cmdline
                and _pid_opens_path(pid, resolved_db)
            )
        ):
            workers.add(pid)
    return workers


def terminate_orphan_library_scans(*, db_path: Path) -> None:
    """Stop stale scan workers that still hold a write lock on the library DB."""
    workers = _scan_worker_pids(db_path=db_path)
    if not workers:
        return
    for pid in sorted(workers):
        _LOG.warning("Terminating orphan library scan pid %d", pid)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        survivors = _scan_worker_pids(db_path=db_path)
        if not survivors:
            return
        time.sleep(0.1)
    for pid in sorted(_scan_worker_pids(db_path=db_path)):
        _LOG.warning("Force-killing orphan library scan pid %d", pid)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
    time.sleep(0.1)
