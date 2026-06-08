"""Playback engine factory — in-process libmpv (#46)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from tunes_player.core.playback.engine import EngineEvent
from tunes_player.engines.mpv import MpvEngine

if TYPE_CHECKING:
    from tunes_player.core.playback.output_profile import PlaybackOutputProfile

EngineCallback = Callable[[EngineEvent], None]


def create_playback_engine(
    *,
    unity_gain: bool = False,
    volume: float = 0.72,
    audio_device: str | None = None,
    use_device_output: bool = False,
    output_profile: PlaybackOutputProfile | None = None,
    on_event: EngineCallback | None = None,
    endpoint_id: str | None = None,
) -> MpvEngine:
    return MpvEngine(
        unity_gain=unity_gain,
        volume=volume,
        audio_device=audio_device,
        use_device_output=use_device_output,
        output_profile=output_profile,
        on_event=on_event,
        endpoint_id=endpoint_id,
    )


def probe_playback_engine() -> str | None:
    """Return a user-facing error message if libmpv cannot load, else None."""
    from tunes_player.engines.mpv import probe_playback_engine as probe_inprocess

    return probe_inprocess()
