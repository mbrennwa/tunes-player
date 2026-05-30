"""libmpv playback via python-mpv."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from tunes_player.core.playback.engine import EngineEvent

if TYPE_CHECKING:
    from mpv import MPV

EngineCallback = Callable[[EngineEvent], None]

_POSITION_INTERVAL_SEC = 0.25


def create_mpv_engine(
    *,
    bit_perfect: bool = False,
    volume: float = 0.72,
    audio_device: str | None = None,
    use_device_output: bool = False,
    on_event: EngineCallback | None = None,
) -> MpvEngine:
    return MpvEngine(
        bit_perfect=bit_perfect,
        volume=volume,
        audio_device=audio_device,
        use_device_output=use_device_output,
        on_event=on_event,
    )


class MpvEngine:
    """Headless mpv instance for music playback."""

    def __init__(
        self,
        *,
        bit_perfect: bool = False,
        volume: float = 0.72,
        audio_device: str | None = None,
        use_device_output: bool = False,
        on_event: EngineCallback | None = None,
    ) -> None:
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
        self._bit_perfect = bit_perfect
        self._volume = volume
        self._software_volume = not bit_perfect and not use_device_output
        self._on_event = on_event
        self._loaded_uri: str | None = None
        self._position_sec = 0.0
        self._duration_sec: float | None = None
        self._playing = False
        self._last_position_emit = 0.0

        options: dict[str, object] = {
            "video": False,
            "vo": "null",
            "keep_open": "yes",
            "idle": True,
            "input_default_bindings": False,
            "input_vo_keyboard": False,
            "ytdl": False,
        }
        # Skip JACK in the probe order — it is rarely used and spams stderr when absent.
        if use_device_output:
            # Route through PipeWire/Pulse so wpctl/pactl sink volume affects playback.
            options["ao"] = "pipewire,pulse,alsa,sndio"
        else:
            options["ao"] = "sndio,pulse,alsa,pipewire"
        if bit_perfect:
            options["volume"] = 100
            options["replaygain"] = "no"
        else:
            options["volume"] = max(0.0, min(100.0, volume * 100.0))

        self._player: MPV = mpv_module.MPV(**options)
        if audio_device:
            self._player.audio_device = audio_device
        self._register_observers()

    def set_audio_device(self, audio_device: str | None) -> None:
        if audio_device:
            self._player.audio_device = audio_device

    def set_event_callback(self, callback: EngineCallback | None) -> None:
        self._on_event = callback

    def load(self, uri: str, *, start_sec: float = 0) -> None:
        self._loaded_uri = uri
        self._position_sec = max(0.0, start_sec)
        self._duration_sec = None
        self._player.play(uri)
        if start_sec > 0:
            self._player.time_pos = start_sec
        self._player.pause = False
        self._playing = True
        self._emit("duration_changed")
        self._emit("position_changed")
        self._emit("playing_changed")

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
        if self._bit_perfect:
            return
        self._apply_software_volume()

    def set_bit_perfect(self, enabled: bool) -> None:
        self._bit_perfect = enabled
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
        self._playing = False
        self._player.terminate()

    def _register_observers(self) -> None:
        player = self._player
        end_file = self._mpv_module.MpvEventEndFile

        @player.property_observer("time-pos")
        def _on_time_pos(_name: str, value: float | None) -> None:
            if value is None:
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

        @player.property_observer("pause")
        def _on_pause(_name: str, value: bool | None) -> None:
            self._playing = value is not True and self._loaded_uri is not None
            self._emit("playing_changed")

        @player.event_callback("end-file")
        def _on_end_file(event: object) -> None:
            end_data = getattr(event, "data", None)
            if end_data is None:
                return
            reason = int(end_data.reason)
            if reason == end_file.EOF:
                if self._loaded_uri is None:
                    return
                self._playing = False
                self._emit("track_finished")
            elif reason == end_file.ERROR:
                self._playing = False
                self._emit("playback_error")

    def _emit(self, event: EngineEvent) -> None:
        if self._on_event is not None:
            self._on_event(event)
