"""Playback engine factory — subprocess mpv via JSON IPC."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from tunes_player.core.playback.engine import EngineEvent
from tunes_player.engines.playback_client import MpvPlaybackClient

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
    ipc_socket_path: Path | None = None,
    endpoint_id: str | None = None,
) -> MpvPlaybackClient:
    return MpvPlaybackClient(
        unity_gain=unity_gain,
        volume=volume,
        audio_device=audio_device,
        use_device_output=use_device_output,
        output_profile=output_profile,
        on_event=on_event,
        ipc_socket_path=ipc_socket_path,
        endpoint_id=endpoint_id,
    )


def probe_playback_engine() -> str | None:
    """Return a user-facing error message if mpv is unavailable, else None."""
    if shutil.which("mpv") is None:
        return "mpv is not installed. Install the mpv package (e.g. apt install mpv)."
    return None
