"""mpv log path and playback diagnostic helpers (#46)."""

from __future__ import annotations

import traceback
from pathlib import Path

MPV_LOG_FILE_NAME = "mpv-playback.log"


def mpv_log_path(data_dir: Path) -> Path:
    return data_dir / MPV_LOG_FILE_NAME


def format_action_provenance(*, depth: int = 4, skip: int = 1) -> str:
    """Return a compact caller chain for diagnostic logs."""
    frames = traceback.extract_stack(limit=skip + depth + 1)[:-1]
    frames = frames[-depth:]
    parts = [
        f"{frame.name}({Path(frame.filename).name}:{frame.lineno})"
        for frame in reversed(frames)
    ]
    return " <- ".join(parts)
