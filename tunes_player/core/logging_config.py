"""Application-wide logging setup."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG = logging.getLogger(__name__)
_APP_LOGGER = "tunes_player"
LOG_FILE_NAME = "tunes-player.log"
LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MiB
LOG_BACKUP_COUNT = 3


def diagnostics_log_path(data_dir: Path) -> Path:
    return data_dir / LOG_FILE_NAME


def configure_logging(data_dir: Path) -> Path:
    """Configure file logging and optional stderr output. Returns the log file path."""
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = diagnostics_log_path(data_dir)

    level_name = os.environ.get("TUNES_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    app_logger = logging.getLogger(_APP_LOGGER)
    app_logger.setLevel(level)
    app_logger.handlers.clear()
    app_logger.propagate = False

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    app_logger.addHandler(file_handler)

    if sys.stderr.isatty():
        console_handler = logging.StreamHandler(sys.stderr)
        stderr_level = level
        if os.environ.get("TUNES_LOG_STDERR", "").lower() not in ("1", "yes", "true"):
            stderr_level = logging.WARNING
        console_handler.setLevel(stderr_level)
        console_handler.setFormatter(formatter)
        app_logger.addHandler(console_handler)
        print(
            f"tunes-player: logging to {log_path} (level={level_name}"
            + (", stderr enabled via TUNES_LOG_STDERR" if stderr_level == level else "")
            + ")",
            file=sys.stderr,
        )

    _LOG.debug("Logging configured: %s (level=%s)", log_path, level_name)
    return log_path
