"""Playback engine protocol — implemented by engines/mpv.py."""

from __future__ import annotations

from typing import Literal, Protocol

EngineEvent = Literal[
    "position_changed",
    "duration_changed",
    "playing_changed",
    "track_finished",
    "playback_error",
]


class PlaybackEngine(Protocol):
    """Load URIs and emit lifecycle events from a background thread."""

    def load(self, uri: str, *, start_sec: float = 0) -> None: ...

    def play(self) -> None: ...

    def pause(self) -> None: ...

    def stop(self) -> None: ...

    def seek(self, position_sec: float) -> None: ...

    def set_volume(self, level: float) -> None: ...

    def set_bit_perfect(self, enabled: bool) -> None: ...

    def get_position(self) -> float: ...

    def get_duration(self) -> float | None: ...

    def is_playing(self) -> bool: ...

    def set_event_callback(self, callback: object) -> None: ...

    def quit(self) -> None: ...
