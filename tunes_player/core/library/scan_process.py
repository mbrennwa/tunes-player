"""Helpers for the library scan subprocess lifecycle."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from pathlib import Path

_LOG = logging.getLogger(__name__)


def _scan_worker_pids(*, db_path: Path) -> set[int]:
    resolved_db = str(db_path.resolve())
    candidates: set[int] = set()
    for needle in (resolved_db, "tunes-library-scan", "run_library_scan"):
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
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8",
                errors="replace",
            )
        except OSError:
            continue
        if (
            "run_library_scan" in cmdline
            or "tunes-library-scan" in cmdline
            or resolved_db in cmdline
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
    time.sleep(0.3)
