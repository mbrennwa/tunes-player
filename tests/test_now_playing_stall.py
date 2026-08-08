"""Honest progress bar when playback soft-stalls (#67)."""

from __future__ import annotations

import time
import unittest

from tunes_player.ui.gtk.now_playing import NowPlayingBar


class NowPlayingStallTests(unittest.TestCase):
    def test_stalled_position_does_not_wall_clock_extrapolate(self) -> None:
        bar = NowPlayingBar.__new__(NowPlayingBar)
        bar._progress_track_id = "t1"
        bar._shown_sec = 10.0
        bar._shown_anchor_sec = 10.0
        bar._shown_anchor_at = time.monotonic() - 5.0
        bar._set_progress_fraction = lambda *_a, **_k: None  # type: ignore[method-assign]

        shown = bar._sync_shown_position(
            track_id="t1",
            reported_sec=10.0,
            duration_sec=180.0,
            is_playing=True,
            position_stalled=True,
        )
        self.assertAlmostEqual(shown, 10.0)

    def test_playing_position_still_wall_clock_extrapolates(self) -> None:
        bar = NowPlayingBar.__new__(NowPlayingBar)
        bar._progress_track_id = "t1"
        bar._shown_sec = 10.0
        bar._shown_anchor_sec = 10.0
        bar._shown_anchor_at = time.monotonic() - 5.0
        bar._set_progress_fraction = lambda *_a, **_k: None  # type: ignore[method-assign]

        shown = bar._sync_shown_position(
            track_id="t1",
            reported_sec=10.0,
            duration_sec=180.0,
            is_playing=True,
            position_stalled=False,
        )
        self.assertGreater(shown, 14.0)

    def test_same_track_reported_drop_rewinds_shown(self) -> None:
        bar = NowPlayingBar.__new__(NowPlayingBar)
        bar._progress_track_id = "t1"
        bar._shown_sec = 25.0
        bar._shown_anchor_sec = 25.0
        bar._shown_anchor_at = time.monotonic()
        bar._set_progress_fraction = lambda *_a, **_k: None  # type: ignore[method-assign]

        shown = bar._sync_shown_position(
            track_id="t1",
            reported_sec=12.0,
            duration_sec=180.0,
            is_playing=True,
            position_stalled=False,
        )
        self.assertAlmostEqual(shown, 12.0)
        self.assertAlmostEqual(bar._shown_sec, 12.0)


if __name__ == "__main__":
    unittest.main()
