"""Best-effort lower I/O priority for background work during playback."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

LOG = logging.getLogger(__name__)
_idle_io_applied = False


def apply_idle_io_priority() -> None:
    """Run background staging/prefetch at idle I/O class when util-linux ionice exists."""
    global _idle_io_applied
    if _idle_io_applied:
        return
    ionice = shutil.which("ionice")
    if ionice is None:
        return
    try:
        subprocess.run(
            [ionice, "-c", "3", "-p", str(os.getpid())],
            check=False,
            capture_output=True,
        )
        _idle_io_applied = True
        LOG.debug("Set idle I/O priority for pid %d", os.getpid())
    except OSError as exc:
        LOG.debug("Could not set idle I/O priority: %s", exc)
