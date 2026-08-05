"""Tests for temporary #75 release-grid rebuild tracing."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from tunes_player.core.grid_trace import (
    grid_trace_enabled,
    log_grid_event,
    log_show_grid_decision,
)


class GridTraceTests(unittest.TestCase):
    def test_enabled_by_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TUNES_GRID_TRACE", None)
            self.assertTrue(grid_trace_enabled())

    def test_disabled_explicitly(self) -> None:
        for value in ("0", "false", "no", "off", "OFF"):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"TUNES_GRID_TRACE": value}):
                    self.assertFalse(grid_trace_enabled())

    def test_enabled_explicitly(self) -> None:
        with mock.patch.dict(os.environ, {"TUNES_GRID_TRACE": "1"}):
            self.assertTrue(grid_trace_enabled())

    def test_log_show_grid_marks_spurious_same_ids(self) -> None:
        ids = ("a", "b", "c")
        previous = ("All Local", ids, None)
        fingerprint = ("All Local", ids, None)
        with mock.patch.dict(os.environ, {"TUNES_GRID_TRACE": "1"}):
            with self.assertLogs("tunes_player.core.grid_trace", level="INFO") as cm:
                log_show_grid_decision(
                    reason="library_updated",
                    action="recreate",
                    fingerprint=fingerprint,
                    previous=previous,
                    at_root=True,
                    on_release_grid=True,
                )
        self.assertTrue(any("spurious_rebuild=True" in line for line in cm.output))

    def test_log_grid_event_respects_disable(self) -> None:
        with mock.patch.dict(os.environ, {"TUNES_GRID_TRACE": "0"}):
            with self.assertRaises(AssertionError):
                with self.assertLogs("tunes_player.core.grid_trace", level="INFO"):
                    log_grid_event("flags_changed", reason="flags_changed_noop")


if __name__ == "__main__":
    unittest.main()
