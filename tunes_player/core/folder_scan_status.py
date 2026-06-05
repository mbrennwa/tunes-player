"""Helpers for per-folder library scan status shown in Settings."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from tunes_player.core.library.scanner import ScanFileError

# Stored in music_folder_last_scan_errors (negative = non-success outcomes).
FOLDER_SCAN_FAILED = -1
FOLDER_SCAN_INCOMPLETE = -2

DIAGNOSTICS_SCAN_HINT = "see Diagnostics"

_SCAN_LOG = logging.getLogger("tunes_player.scan")


def log_folder_scan_failure(
    folder: str,
    *,
    errors: int,
    log_path: Path,
    fatal_error: str | None = None,
    file_errors: Sequence[ScanFileError] = (),
) -> None:
    """Append scan failure details to the diagnostics log file."""
    log_ref = str(log_path)
    if fatal_error:
        _SCAN_LOG.error(
            "Library scan failed for %s: %s",
            folder,
            fatal_error.strip(),
        )
        _SCAN_LOG.error("Scan diagnostics: %s", log_ref)
        return

    if errors <= 0 and not file_errors:
        return

    _SCAN_LOG.error(
        "Library scan of %s finished with %d file error(s)",
        folder,
        errors,
    )
    for item in file_errors:
        _SCAN_LOG.error("  %s: %s", item.path, item.reason)
    remaining = errors - len(file_errors)
    if remaining > 0:
        _SCAN_LOG.error("  (%d additional file error(s) not listed)", remaining)
    _SCAN_LOG.error("Scan diagnostics: %s", log_ref)


def format_folder_last_scan_line(
    *,
    scanned_at: float | None,
    errors: int | None,
) -> str:
    if scanned_at is None:
        return "Last scan: never"

    stamp = datetime.fromtimestamp(scanned_at).strftime("%Y-%m-%d %H:%M")
    if errors is None:
        detail = "errors unknown"
    elif errors == FOLDER_SCAN_INCOMPLETE:
        detail = "incomplete"
    elif errors < 0:
        detail = "scan failed"
    elif errors == 0:
        detail = "no errors"
    elif errors == 1:
        detail = "1 error"
    else:
        detail = f"{errors} errors"

    line = f"Last scan: {stamp} · {detail}"
    if errors is not None and errors != FOLDER_SCAN_INCOMPLETE and (errors > 0 or errors < 0):
        line = f"{line} · {DIAGNOSTICS_SCAN_HINT}"
    return line
