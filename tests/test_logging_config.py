"""Tests for application logging configuration (#72, #76)."""

from __future__ import annotations

import logging
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import patch

from platformdirs import user_state_dir

from tunes_player.core.config import APP_NAME, ConfigManager
from tunes_player.core.logging_config import (
    LOG_BACKUP_COUNT,
    LOG_FILE_NAME,
    LOG_MAX_BYTES,
    configure_logging,
    diagnostics_log_path,
    migrate_legacy_diagnostics_log,
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
            state_dir = Path(tmp)
            log_path = configure_logging(state_dir)

            self.assertEqual(log_path, diagnostics_log_path(state_dir))
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

    def test_migrate_legacy_diagnostics_log_moves_file_and_backups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_dir = root / "share"
            state_dir = root / "state"
            legacy_dir.mkdir()
            (legacy_dir / LOG_FILE_NAME).write_text("current\n", encoding="utf-8")
            (legacy_dir / f"{LOG_FILE_NAME}.1").write_text("old\n", encoding="utf-8")

            migrate_legacy_diagnostics_log(legacy_dir=legacy_dir, state_dir=state_dir)

            self.assertFalse((legacy_dir / LOG_FILE_NAME).exists())
            self.assertFalse((legacy_dir / f"{LOG_FILE_NAME}.1").exists())
            self.assertEqual(
                (state_dir / LOG_FILE_NAME).read_text(encoding="utf-8"),
                "current\n",
            )
            self.assertEqual(
                (state_dir / f"{LOG_FILE_NAME}.1").read_text(encoding="utf-8"),
                "old\n",
            )

    def test_migrate_legacy_diagnostics_log_keeps_existing_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_dir = root / "share"
            state_dir = root / "state"
            legacy_dir.mkdir()
            state_dir.mkdir()
            (legacy_dir / LOG_FILE_NAME).write_text("legacy\n", encoding="utf-8")
            (state_dir / LOG_FILE_NAME).write_text("state\n", encoding="utf-8")

            migrate_legacy_diagnostics_log(legacy_dir=legacy_dir, state_dir=state_dir)

            self.assertTrue((legacy_dir / LOG_FILE_NAME).exists())
            self.assertEqual(
                (state_dir / LOG_FILE_NAME).read_text(encoding="utf-8"),
                "state\n",
            )

    def test_configure_logging_migrates_legacy_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_dir = root / "share"
            state_dir = root / "state"
            legacy_dir.mkdir()
            (legacy_dir / LOG_FILE_NAME).write_text("migrated\n", encoding="utf-8")

            log_path = configure_logging(state_dir, legacy_data_dir=legacy_dir)

            self.assertEqual(log_path, state_dir / LOG_FILE_NAME)
            self.assertEqual(log_path.read_text(encoding="utf-8"), "migrated\n")
            self.assertFalse((legacy_dir / LOG_FILE_NAME).exists())

    def test_config_manager_state_dir_uses_xdg_state(self) -> None:
        with patch(
            "tunes_player.core.config.user_state_dir",
            return_value="/tmp/fake-state/tunes-player",
        ):
            manager = ConfigManager(Path("/tmp/unused-config.json"))
            self.assertEqual(manager.state_dir, Path("/tmp/fake-state/tunes-player"))
        self.assertEqual(
            str(ConfigManager(Path("/tmp/unused-config.json")).state_dir),
            user_state_dir(APP_NAME),
        )


if __name__ == "__main__":
    unittest.main()
