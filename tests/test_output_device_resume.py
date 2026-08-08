"""Output-device rebuild resumes mid-track (#173)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tunes_player.core.backends.playable import PlayableSource
from tunes_player.core.config import ConfigManager
from tunes_player.core.models import Source, Track
from tunes_player.core.playback.output_profile import (
    PlaybackOutputProfile,
    PlaybackPathInfo,
)
from tunes_player.core.services import PlayerService


class _ResumeEngine:
    def __init__(self, *, position_sec: float = 0.0, playing: bool = False) -> None:
        self._position_sec = position_sec
        self._playing = playing
        self.load_calls: list[dict[str, object]] = []
        self.paused = False

    def get_position(self) -> float:
        return self._position_sec

    def query_time_pos(self) -> float:
        return self._position_sec

    def get_duration(self) -> float | None:
        return 180.0

    def is_playing(self) -> bool:
        return self._playing and not self.paused

    def load(
        self,
        uri: str,
        *,
        start_sec: float = 0,
        output_profile: object = None,
    ) -> None:
        self.load_calls.append(
            {
                "uri": uri,
                "start_sec": start_sec,
                "output_profile": output_profile,
            }
        )
        self._position_sec = start_sec
        self._playing = True
        self.paused = False

    def pause(self) -> None:
        self.paused = True
        self._playing = False

    def play(self) -> None:
        self.paused = False
        self._playing = True

    def quit(self) -> None:
        return None

    def set_event_callback(self, callback: object) -> None:
        return None

    def set_volume(self, level: float) -> None:
        return None

    def set_bit_perfect(self, enabled: bool) -> None:
        return None

    def set_output_profile(self, profile: object) -> None:
        return None

    def set_playback_path_context(self, context: object) -> None:
        return None


class OutputDeviceResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        config = ConfigManager(Path(self._tmpdir.name) / "config.toml")
        config.load()
        self._service = PlayerService(config=config)
        self._track = Track(
            id="local:file:one",
            title="Track",
            artist_name="Artist",
            release_title="Album",
            source=Source.LOCAL,
            duration_sec=180.0,
        )
        self._profile = PlaybackOutputProfile(
            direct_alsa=False,
            use_exclusive=False,
            allow_resample=True,
        )
        self._path_info = PlaybackPathInfo(
            bit_perfect_playback=False,
            playback_note="via PipeWire",
        )
        self._source = PlayableSource(
            uri="file:///music/track.flac",
            metadata=self._track,
            format_label="FLAC",
        )

    def tearDown(self) -> None:
        self._service._engine = None
        self._service.shutdown()
        self._tmpdir.cleanup()

    def test_rebuild_reloads_at_captured_position(self) -> None:
        old = _ResumeEngine(position_sec=17.5, playing=True)
        new = _ResumeEngine()
        self._service._engine = old
        self._service._current_track = self._track
        self._service._position_sec = 17.5
        self._service._is_playing = True
        self._service._playback_intended = True

        def ensure_engine() -> _ResumeEngine:
            self._service._engine = new
            return new

        self._service._ensure_engine = ensure_engine  # type: ignore[method-assign]
        self._service._has_device_volume = lambda **_k: False  # type: ignore[method-assign]
        self._service._remember_stream_metadata = lambda *_a, **_k: None  # type: ignore[method-assign]
        self._service._compute_playback_profile_for_track = (  # type: ignore[method-assign]
            lambda *_a, **_k: (self._profile, self._path_info)
        )
        self._service._apply_path_info = lambda *_a, **_k: None  # type: ignore[method-assign]
        self._service._acquire_exclusive_session_if_needed = (  # type: ignore[method-assign]
            lambda *_a, **_k: None
        )
        self._service._configure_engine_playback_path = (  # type: ignore[method-assign]
            lambda *_a, **_k: None
        )
        self._service._playback_target_for_engine = (  # type: ignore[method-assign]
            lambda source: source.playback_target
        )
        self._service._release_exclusive_session = lambda: None  # type: ignore[method-assign]

        with patch(
            "tunes_player.core.services.resolve_track",
            return_value=self._source,
        ):
            self._service._rebuild_engine_for_output_change()

        self.assertEqual(len(new.load_calls), 1)
        self.assertAlmostEqual(float(new.load_calls[0]["start_sec"]), 17.5)
        self.assertAlmostEqual(self._service._position_sec, 17.5)
        self.assertTrue(self._service._is_playing)
        self.assertTrue(self._service._playback_intended)
        self.assertFalse(new.paused)


if __name__ == "__main__":
    unittest.main()
