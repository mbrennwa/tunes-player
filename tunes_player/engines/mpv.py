"""libmpv playback via python-mpv."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from tunes_player.core.playback.engine import EngineEvent
from tunes_player.core.playback.output_profile import PlaybackOutputProfile
from tunes_player.core.playback.playback_path import (
    PlaybackPathContext,
    derive_playback_path_info,
    read_negotiated_playback_state,
)

if TYPE_CHECKING:
    from mpv import MPV
    from tunes_player.core.playback.output_profile import PlaybackPathInfo

EngineCallback = Callable[[EngineEvent], None]

_POSITION_INTERVAL_SEC = 0.1


def create_mpv_engine(
    *,
    unity_gain: bool = False,
    volume: float = 0.72,
    audio_device: str | None = None,
    use_device_output: bool = False,
    output_profile: PlaybackOutputProfile | None = None,
    on_event: EngineCallback | None = None,
) -> MpvEngine:
    return MpvEngine(
        unity_gain=unity_gain,
        volume=volume,
        audio_device=audio_device,
        use_device_output=use_device_output,
        output_profile=output_profile,
        on_event=on_event,
    )


def probe_playback_engine() -> str | None:
    """Return a user-facing error message if playback is unavailable, else None."""
    from tunes_player.engines.factory import probe_playback_engine as probe_ipc

    return probe_ipc()


class MpvEngine:
    """Headless mpv instance for music playback."""

    def __init__(
        self,
        *,
        unity_gain: bool = False,
        volume: float = 0.72,
        audio_device: str | None = None,
        use_device_output: bool = False,
        output_profile: PlaybackOutputProfile | None = None,
        on_event: EngineCallback | None = None,
        # Back-compat for tests
        bit_perfect: bool | None = None,
    ) -> None:
        if bit_perfect is not None:
            unity_gain = bit_perfect
        try:
            import mpv as mpv_module
        except OSError as exc:
            raise RuntimeError(
                "libmpv is not installed. Install the mpv package (e.g. apt install mpv libmpv2)."
            ) from exc
        except ImportError as exc:
            raise RuntimeError(
                "python-mpv is not installed. Run: pip install python-mpv"
            ) from exc

        self._mpv_module = mpv_module
        self._unity_gain = unity_gain
        self._volume = volume
        self._output_profile = output_profile
        self._use_device_output = use_device_output
        self._software_volume = not unity_gain and not use_device_output
        self._on_event = on_event
        self._loaded_uri: str | None = None
        self._position_sec = 0.0
        self._duration_sec: float | None = None
        self._playing = False
        self._last_position_emit = 0.0
        self._track_end_signaled = False
        self._path_context: PlaybackPathContext | None = None
        self._path_info: PlaybackPathInfo | None = None

        options: dict[str, object] = {
            "video": False,
            "vo": "null",
            "keep_open": "yes",
            "idle": True,
            "input_default_bindings": False,
            "input_vo_keyboard": False,
            "ytdl": False,
        }
        options.update(_base_audio_options(output_profile, use_device_output))
        if unity_gain:
            options["volume"] = 100
            options["replaygain"] = "no"
        else:
            options["volume"] = max(0.0, min(100.0, volume * 100.0))

        self._player: MPV = mpv_module.MPV(**options)
        self._configure_stream_demuxer()
        if audio_device:
            self._player.audio_device = audio_device
        self._register_observers()

    def _configure_stream_demuxer(self) -> None:
        try:
            self._player._set_property(
                "stream-lavf-o",
                {"protocol_whitelist": "file,crypto,data,https,tcp,tls"},
            )
        except (TypeError, AttributeError):
            logging.getLogger(__name__).warning(
                "Could not set mpv stream-lavf-o for HTTPS streaming"
            )

    def set_audio_device(self, audio_device: str | None) -> None:
        if audio_device:
            self._player.audio_device = audio_device

    def set_output_profile(self, profile: PlaybackOutputProfile | None) -> None:
        self._output_profile = profile
        if profile is None or not profile.direct_alsa:
            return
        self._player.ao = "alsa"
        if profile.use_exclusive:
            self._player.audio_exclusive = "yes"
        else:
            self._player.audio_exclusive = "no"

    def set_playback_path_context(self, context: PlaybackPathContext | None) -> None:
        self._path_context = context

    def get_playback_path_info(self) -> PlaybackPathInfo | None:
        return self._path_info

    def refresh_playback_path_info(self) -> None:
        profile = self._output_profile
        context = self._path_context
        if profile is None or context is None:
            return
        self._path_info = derive_playback_path_info(
            file_meta=context.file_meta,
            profile=profile,
            negotiated=read_negotiated_playback_state(self._get_mpv_property),
            endpoint_id=context.endpoint_id,
            device_volume=context.device_volume,
            mpv_soft_volume=context.mpv_soft_volume,
        )

    def _refresh_playback_path_info(self) -> None:
        previous = self._path_info
        self.refresh_playback_path_info()
        if self._path_info != previous:
            self._emit("playback_path_changed")

    def _get_mpv_property(self, name: str) -> object:
        attr = name.replace("-", "_")
        try:
            return getattr(self._player, attr)
        except AttributeError:
            return None

    def set_event_callback(self, callback: EngineCallback | None) -> None:
        self._on_event = callback

    def load(
        self,
        uri: str,
        *,
        start_sec: float = 0,
        output_profile: PlaybackOutputProfile | None = None,
    ) -> None:
        profile = output_profile if output_profile is not None else self._output_profile
        if profile is not None:
            self._apply_track_format(profile)
        self._loaded_uri = uri
        self._track_end_signaled = False
        self._position_sec = max(0.0, start_sec)
        self._duration_sec = None
        self._last_position_emit = 0.0
        self._player.play(uri)
        if start_sec > 0:
            self._player.time_pos = start_sec
        self._player.pause = False
        self._playing = True
        self._refresh_playback_path_info()
        self._emit("duration_changed")
        self._emit("playing_changed")

    def _apply_track_format(self, profile: PlaybackOutputProfile) -> None:
        if not profile.direct_alsa:
            return
        log = logging.getLogger(__name__)
        self._player.replaygain = "no"
        if self._unity_gain:
            self._player.volume = 100
        else:
            self._apply_software_volume()
        if profile.allow_resample:
            try:
                self._player.alsa_resample = "yes"
            except (AttributeError, TypeError) as exc:
                log.warning("mpv rejected alsa-resample: %s", exc)
        else:
            try:
                self._player.alsa_resample = "no"
            except (AttributeError, TypeError) as exc:
                log.warning("mpv rejected alsa-resample: %s", exc)
        if profile.target_rate is not None:
            try:
                self._player.audio_samplerate = profile.target_rate
            except TypeError as exc:
                log.warning("mpv rejected audio-samplerate %s: %s", profile.target_rate, exc)
        if profile.audio_format is not None:
            try:
                self._player.audio_format = profile.audio_format
            except TypeError as exc:
                log.warning("mpv rejected audio-format %s: %s", profile.audio_format, exc)
        if profile.target_channels is not None and profile.target_channels != 2:
            try:
                self._player.audio_channels = profile.target_channels
            except TypeError as exc:
                log.warning(
                    "mpv rejected audio-channels %s: %s",
                    profile.target_channels,
                    exc,
                )

    def play(self) -> None:
        if self._loaded_uri is None:
            return
        self._player.pause = False
        self._playing = True
        self._emit("playing_changed")

    def pause(self) -> None:
        if self._loaded_uri is None:
            return
        self._player.pause = True
        pos = self._player.time_pos
        self._playing = False
        if pos is not None:
            self._position_sec = float(pos)
        self._emit("playing_changed")
        self._emit("position_changed")

    def stop(self) -> None:
        self._player.command("stop")
        self._track_end_signaled = False
        self._loaded_uri = None
        self._playing = False
        self._position_sec = 0.0
        self._duration_sec = None
        self._emit("playing_changed")
        self._emit("position_changed")
        self._emit("duration_changed")

    def seek(self, position_sec: float) -> None:
        if self._loaded_uri is None:
            return
        target = max(0.0, position_sec)
        self._player.time_pos = target
        self._position_sec = target
        self._emit("position_changed")

    def set_volume(self, level: float) -> None:
        self._volume = max(0.0, min(1.0, level))
        if self._unity_gain:
            return
        self._apply_software_volume()

    def set_bit_perfect(self, enabled: bool) -> None:
        """Unity gain in mpv (no soft volume)."""
        self._unity_gain = enabled
        self._software_volume = not enabled and not self._use_device_output
        self._player.replaygain = "no"
        if enabled or not self._software_volume:
            self._player.volume = 100
        else:
            self._apply_software_volume()

    def _apply_software_volume(self) -> None:
        if not self._software_volume:
            return
        gain = max(0.0, min(1.0, self._volume))
        self._player.volume = gain * 100.0

    def get_position(self) -> float:
        pos = self._player.time_pos
        if pos is not None:
            self._position_sec = float(pos)
        return self._position_sec

    def get_duration(self) -> float | None:
        duration = self._player.duration
        if duration is not None and duration > 0:
            self._duration_sec = float(duration)
        return self._duration_sec

    def is_playing(self) -> bool:
        if self._loaded_uri is None:
            return False
        paused = self._player.pause
        if paused is None:
            return self._playing
        return not bool(paused)

    def quit(self) -> None:
        self._loaded_uri = None
        self._track_end_signaled = False
        self._playing = False
        self._player.terminate()

    def _signal_track_finished(self) -> None:
        if self._loaded_uri is None or self._track_end_signaled:
            return
        self._track_end_signaled = True
        self._playing = False
        self._emit("track_finished")

    @staticmethod
    def _eof_reached(value: object) -> bool:
        if value is True:
            return True
        if isinstance(value, str):
            return value.lower() in ("yes", "true", "1")
        return False

    def _register_observers(self) -> None:
        player = self._player
        end_file = self._mpv_module.MpvEventEndFile

        @player.property_observer("time-pos")
        def _on_time_pos(_name: str, value: float | None) -> None:
            if value is None or value < 0:
                return
            self._position_sec = float(value)
            now = time.monotonic()
            if now - self._last_position_emit >= _POSITION_INTERVAL_SEC:
                self._last_position_emit = now
                self._emit("position_changed")

        @player.property_observer("duration")
        def _on_duration(_name: str, value: float | None) -> None:
            if value is None or value <= 0:
                return
            self._duration_sec = float(value)
            self._emit("duration_changed")
            self._refresh_playback_path_info()

        @player.property_observer("pause")
        def _on_pause(_name: str, value: bool | None) -> None:
            self._playing = value is not True and self._loaded_uri is not None
            self._emit("playing_changed")

        @player.property_observer("eof-reached")
        def _on_eof_reached(_name: str, value: object) -> None:
            if self._eof_reached(value):
                self._signal_track_finished()

        @player.event_callback("end-file")
        def _on_end_file(event: object) -> None:
            end_data = getattr(event, "data", None)
            if end_data is None:
                return
            reason = int(end_data.reason)
            if reason == end_file.EOF:
                self._signal_track_finished()
            elif reason == end_file.ERROR:
                self._playing = False
                self._emit("playback_error")

    def _emit(self, event: EngineEvent) -> None:
        if self._on_event is not None:
            self._on_event(event)


def _base_audio_options(
    profile: PlaybackOutputProfile | None,
    use_device_output: bool,
) -> dict[str, object]:
    if profile is not None and profile.direct_alsa:
        opts: dict[str, object] = {"ao": "alsa", "replaygain": "no"}
        if profile.use_exclusive:
            opts["audio_exclusive"] = "yes"
        return opts
    if use_device_output:
        return {"ao": "pipewire,pulse,alsa,sndio"}
    return {"ao": "sndio,pulse,alsa,pipewire"}
