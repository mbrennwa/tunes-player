"""Helpers for per-folder library scan status shown in Settings."""

from __future__ import annotations

from datetime import datetime


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
    elif errors < 0:
        detail = "scan failed"
    elif errors == 0:
        detail = "no errors"
    elif errors == 1:
        detail = "1 error"
    else:
        detail = f"{errors} errors"
    return f"Last scan: {stamp} · {detail}"
