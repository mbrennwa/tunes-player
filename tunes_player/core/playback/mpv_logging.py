"""mpv subprocess log file helpers."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
import traceback
from pathlib import Path

LOG = logging.getLogger(__name__)

MPV_LOG_FILE_NAME = "mpv-playback.log"
_MPV_LOG_TAIL_LINES = 20
_MPV_DISCONNECT_ARCHIVE_PREFIX = "mpv-playback-disconnect-"


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


def format_action_provenance(*, depth: int = 4, skip: int = 1) -> str:
    """Return a compact caller chain for diagnostic logs."""
    frames = traceback.extract_stack(limit=skip + depth + 1)[:-1]
    frames = frames[-depth:]
    parts = [
        f"{frame.name}({Path(frame.filename).name}:{frame.lineno})"
        for frame in reversed(frames)
    ]
    return " <- ".join(parts)


def _pgrep_pids(pattern: str) -> list[int]:
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    return sorted({int(pid) for pid in result.stdout.split() if pid.strip().isdigit()})


def _pgrep_exact(name: str) -> list[int]:
    try:
        result = subprocess.run(
            ["pgrep", "-x", name],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    return sorted({int(pid) for pid in result.stdout.split() if pid.strip().isdigit()})


def _tunes_player_pids() -> list[int]:
    """Match the app entrypoint, not unrelated paths containing 'tunes-player'."""
    return _pgrep_pids(r"bin/tunes-player$")


def describe_process_snapshot() -> str:
    """Summarize concurrent tunes-player and mpv processes for disconnect diagnosis."""
    tunes_pids = _tunes_player_pids()
    mpv_pids = _pgrep_exact("mpv")
    return (
        f"own_pid={os.getpid()} "
        f"tunes_player_pids={tunes_pids} tunes_player_count={len(tunes_pids)} "
        f"mpv_pids={mpv_pids} mpv_count={len(mpv_pids)}"
    )


def archive_mpv_log(log_path: Path) -> Path | None:
    """Preserve the current mpv log after an unexpected disconnect."""
    try:
        if not log_path.is_file() or log_path.stat().st_size == 0:
            return None
    except OSError:
        return None

    stamp = time.strftime("%Y%m%d-%H%M%S")
    archived = log_path.with_name(f"{_MPV_DISCONNECT_ARCHIVE_PREFIX}{stamp}.log")
    suffix = 1
    while archived.exists():
        archived = log_path.with_name(
            f"{_MPV_DISCONNECT_ARCHIVE_PREFIX}{stamp}-{suffix}.log"
        )
        suffix += 1
    try:
        shutil.copy2(log_path, archived)
    except OSError as exc:
        LOG.warning("Could not archive mpv log %s: %s", log_path, exc)
        return None
    return archived
