"""Application-wide logging setup."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_LOG = logging.getLogger(__name__)
_APP_LOGGER = "tunes_player"


def configure_logging(data_dir: Path) -> Path:
    """Configure file logging and optional stderr output. Returns the log file path."""
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "tunes-player.log"

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

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    app_logger.addHandler(file_handler)

    if sys.stderr.isatty():
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(logging.WARNING)
        console_handler.setFormatter(formatter)
        app_logger.addHandler(console_handler)

    _LOG.debug("Logging configured: %s (level=%s)", log_path, level_name)
    return log_path
