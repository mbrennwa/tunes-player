"""Tests for application logging configuration (#72)."""

from __future__ import annotations

import logging
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path

from tunes_player.core.logging_config import (
    LOG_BACKUP_COUNT,
    LOG_MAX_BYTES,
    configure_logging,
    diagnostics_log_path,
)

_APP_LOGGER = "tunes_player"


class LoggingConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        app_logger = logging.getLogger(_APP_LOGGER)
        for handler in list(app_logger.handlers):
            handler.close()
        app_logger.handlers.clear()

    def test_configure_logging_uses_rotating_file_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            log_path = configure_logging(data_dir)

            self.assertEqual(log_path, diagnostics_log_path(data_dir))
            app_logger = logging.getLogger(_APP_LOGGER)
            file_handlers = [
                h for h in app_logger.handlers if isinstance(h, RotatingFileHandler)
            ]
            self.assertEqual(len(file_handlers), 1)
            handler = file_handlers[0]
            self.assertEqual(handler.maxBytes, LOG_MAX_BYTES)
            self.assertEqual(handler.backupCount, LOG_BACKUP_COUNT)
            self.assertEqual(LOG_MAX_BYTES, 5 * 1024 * 1024)
            self.assertEqual(LOG_BACKUP_COUNT, 3)


if __name__ == "__main__":
    unittest.main()
