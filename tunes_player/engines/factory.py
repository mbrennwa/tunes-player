"""Playback engine factory — in-process libmpv by default (#46)."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Union

from tunes_player.core.playback.engine import EngineEvent
from tunes_player.engines.mpv import MpvEngine
from tunes_player.engines.playback_client import MpvPlaybackClient

if TYPE_CHECKING:
    from tunes_player.core.playback.output_profile import PlaybackOutputProfile

EngineCallback = Callable[[EngineEvent], None]
PlaybackEngineImpl = Union[MpvEngine, MpvPlaybackClient]


def playback_engine_backend() -> str:
    """Return ``inprocess`` (default) or ``subprocess``."""
    return os.environ.get("TUNES_PLAYBACK_ENGINE", "inprocess").strip().casefold()


def playback_engine_uses_worker_thread() -> bool:
    """Subprocess mpv runs on a dedicated thread; in-process uses the GTK/main thread."""
    return playback_engine_backend() == "subprocess"


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
) -> PlaybackEngineImpl:
    if playback_engine_backend() == "subprocess":
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
    """Return a user-facing error message if playback is unavailable, else None."""
    if playback_engine_backend() == "subprocess":
        if shutil.which("mpv") is None:
            return "mpv is not installed. Install the mpv package (e.g. apt install mpv)."
        return None
    from tunes_player.engines.mpv import probe_playback_engine as probe_inprocess

    return probe_inprocess()
