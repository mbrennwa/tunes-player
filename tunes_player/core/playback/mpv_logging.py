"""mpv subprocess log file helpers."""

from __future__ import annotations

import os
from pathlib import Path

MPV_LOG_FILE_NAME = "mpv-playback.log"
_MPV_LOG_TAIL_LINES = 20


def mpv_log_path(data_dir: Path) -> Path:
    return data_dir / MPV_LOG_FILE_NAME


def mpv_log_path_for_socket(socket_path: Path) -> Path:
    return mpv_log_path(socket_path.parent)


def mpv_msg_level_from_env() -> str | None:
    value = os.environ.get("TUNES_MPV_MSG_LEVEL", "").strip()
    return value or None


def mpv_logging_cli_args(*, log_path: Path) -> list[str]:
    """Return mpv CLI flags for file logging and optional verbose modules."""
    args = [f"--log-file={log_path}"]
    msg_level = mpv_msg_level_from_env()
    if msg_level is not None:
        args.append(f"--msg-level={msg_level}")
    return args


def prepare_mpv_log_file(log_path: Path) -> None:
    """Truncate any previous mpv log so each subprocess starts with a fresh file."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")


def tail_mpv_log(log_path: Path, *, max_lines: int = _MPV_LOG_TAIL_LINES) -> list[str]:
    """Return the last *max_lines* from the mpv log file, if readable."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()
    if not lines:
        return []
    return lines[-max_lines:]
