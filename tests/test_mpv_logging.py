"""Tests for mpv subprocess logging helpers."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tunes_player.core.playback.mpv_logging import (
    MPV_LOG_FILE_NAME,
    archive_mpv_log,
    describe_process_snapshot,
    format_action_provenance,
    mpv_log_path,
    mpv_log_path_for_socket,
    mpv_logging_cli_args,
    mpv_msg_level_from_env,
    prepare_mpv_log_file,
    tail_mpv_log,
)


class MpvLoggingTests(unittest.TestCase):
    def test_mpv_log_path(self) -> None:
        data_dir = Path("/tmp/tunes-data")
        self.assertEqual(mpv_log_path(data_dir), data_dir / MPV_LOG_FILE_NAME)
        self.assertEqual(
            mpv_log_path_for_socket(data_dir / "mpv-playback.sock"),
            data_dir / MPV_LOG_FILE_NAME,
        )

    def test_mpv_logging_cli_args_default(self) -> None:
        log_path = Path("/tmp/tunes-data/mpv-playback.log")
        self.assertEqual(
            mpv_logging_cli_args(log_path=log_path),
            [f"--log-file={log_path}"],
        )

    def test_mpv_logging_cli_args_with_msg_level(self) -> None:
        log_path = Path("/tmp/tunes-data/mpv-playback.log")
        with mock.patch.dict(
            os.environ,
            {"TUNES_MPV_MSG_LEVEL": "cplayer=v,playlist=debug"},
            clear=False,
        ):
            self.assertEqual(
                mpv_logging_cli_args(log_path=log_path),
                [
                    f"--log-file={log_path}",
                    "--msg-level=cplayer=v,playlist=debug",
                ],
            )

    def test_mpv_msg_level_from_env_empty(self) -> None:
        with mock.patch.dict(os.environ, {"TUNES_MPV_MSG_LEVEL": "  "}, clear=False):
            self.assertIsNone(mpv_msg_level_from_env())

    def test_prepare_and_tail_mpv_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / MPV_LOG_FILE_NAME
            prepare_mpv_log_file(log_path)
            self.assertEqual(log_path.read_text(encoding="utf-8"), "")
            log_path.write_text("line1\nline2\nline3\n", encoding="utf-8")
            self.assertEqual(tail_mpv_log(log_path, max_lines=2), ["line2", "line3"])

    def test_format_action_provenance_includes_caller(self) -> None:
        def nested() -> str:
            return format_action_provenance(depth=2, skip=1)

        provenance = nested()
        self.assertIn("nested(", provenance)
        self.assertIn("test_mpv_logging.py", provenance)

    def test_archive_mpv_log_preserves_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / MPV_LOG_FILE_NAME
            log_path.write_text("disconnect context\n", encoding="utf-8")
            archived = archive_mpv_log(log_path)
            self.assertIsNotNone(archived)
            assert archived is not None
            self.assertTrue(archived.name.startswith("mpv-playback-disconnect-"))
            self.assertEqual(
                archived.read_text(encoding="utf-8"),
                "disconnect context\n",
            )

    def test_archive_mpv_log_skips_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / MPV_LOG_FILE_NAME
            prepare_mpv_log_file(log_path)
            self.assertIsNone(archive_mpv_log(log_path))

    def test_describe_process_snapshot_includes_own_pid(self) -> None:
        snapshot = describe_process_snapshot()
        self.assertIn(f"own_pid={os.getpid()}", snapshot)
        self.assertIn("tunes_player_count=", snapshot)
        self.assertIn("mpv_count=", snapshot)


if __name__ == "__main__":
    unittest.main()
