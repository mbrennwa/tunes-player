"""Playback engine protocol — implemented by engines/mpv.py."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from tunes_player.core.playback.output_profile import PlaybackOutputProfile

EngineEvent = Literal[
    "position_changed",
    "duration_changed",
    "playing_changed",
    "track_started",
    "track_finished",
    "playback_error",
    "playback_path_changed",
]


class PlaybackEngine(Protocol):
    """Load URIs and emit lifecycle events from a background thread."""

    def load(
        self,
        uri: str,
        *,
        start_sec: float = 0,
        output_profile: PlaybackOutputProfile | None = None,
    ) -> None: ...

    def set_output_profile(self, profile: PlaybackOutputProfile | None) -> None: ...

    def play(self) -> None: ...

    def pause(self) -> None: ...

    def stop(self) -> None: ...

    def seek(self, position_sec: float, *, resume: bool | None = None) -> None: ...

    def set_volume(self, level: float) -> None: ...

    def set_bit_perfect(self, enabled: bool) -> None: ...

    def get_position(self) -> float: ...

    def query_time_pos(self) -> float: ...

    def get_duration(self) -> float | None: ...

    def max_seek_position_sec(self) -> float | None: ...

    def is_playing(self) -> bool: ...

    def set_event_callback(self, callback: object) -> None: ...

    def quit(self) -> None: ...
