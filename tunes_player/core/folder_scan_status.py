"""Helpers for per-folder library scan status shown in Settings."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal

from tunes_player.core.library.scanner import ScanFileError

# Stored in music_folder_last_scan_errors (negative = non-success outcomes).
FOLDER_SCAN_FAILED = -1
FOLDER_SCAN_INCOMPLETE = -2

DIAGNOSTICS_SCAN_HINT = "see Settings → About"

_SCAN_LOG = logging.getLogger("tunes_player.scan")

ScanKind = Literal["full", "incremental"]


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


def _format_coverage(
    *,
    indexed_files: int | None,
    catalog_total: int | None,
) -> str | None:
    if indexed_files is None:
        return None
    if catalog_total is not None and catalog_total > 0:
        return f"{indexed_files:,} / {catalog_total:,} files indexed"
    if indexed_files > 0:
        return f"{indexed_files:,} files indexed"
    return None


def _format_status_detail(
    *,
    errors: int | None,
    indexed_files: int | None,
    catalog_total: int | None,
    last_scan_kind: ScanKind | None,
) -> str:
    if errors == FOLDER_SCAN_INCOMPLETE:
        return "incomplete"
    if errors is not None and errors < 0:
        return "scan failed"
    if errors is None:
        return "incomplete"

    catalog_known = catalog_total is not None and catalog_total > 0
    fully_indexed = (
        catalog_known
        and indexed_files is not None
        and indexed_files >= catalog_total
    )
    last_full_scan = last_scan_kind == "full"

    if last_full_scan and fully_indexed and errors == 0:
        return "complete"
    if errors == 1:
        return "1 error"
    if errors > 1:
        return f"{errors} errors"
    return "incomplete"


def format_folder_last_scan_line(
    *,
    scanned_at: float | None,
    errors: int | None,
    indexed_files: int | None = None,
    catalog_total: int | None = None,
    last_scan_kind: ScanKind | None = None,
) -> str:
    coverage = _format_coverage(
        indexed_files=indexed_files,
        catalog_total=catalog_total,
    )
    if scanned_at is None:
        if coverage is None:
            return "Last scan: never"
        return f"Last scan: never · {coverage} · incomplete"

    stamp = datetime.fromtimestamp(scanned_at).strftime("%Y-%m-%d %H:%M")
    detail = _format_status_detail(
        errors=errors,
        indexed_files=indexed_files,
        catalog_total=catalog_total,
        last_scan_kind=last_scan_kind,
    )

    if detail == "complete" and indexed_files is not None and indexed_files > 0:
        return f"Last scan: {stamp} · {indexed_files:,} files"

    parts = [f"Last scan: {stamp}"]
    if coverage is not None:
        parts.append(coverage)
    parts.append(detail)

    line = " · ".join(parts)
    if errors is not None and errors != FOLDER_SCAN_INCOMPLETE and (errors > 0 or errors < 0):
        line = f"{line} · {DIAGNOSTICS_SCAN_HINT}"
    return line
