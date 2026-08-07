"""In-process libmpv playback via python-mpv (#46)."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from tunes_player.core.playback.buffer_policy import (
    classify_playback_uri,
    direct_alsa_engine_options,
    mpv_options_for_input,
)
from tunes_player.core.playback.engine import EngineEvent
from tunes_player.core.playback.mpv_events import (
    end_file_triggers_playback_error,
    end_file_triggers_track_finished,
)
from tunes_player.core.playback.mpv_cli import base_audio_options
from tunes_player.core.playback.playback_path import (
    NegotiatedPlaybackState,
    PlaybackPathContext,
    derive_playback_path_info,
    read_negotiated_playback_state,
)

if TYPE_CHECKING:
    from mpv import MPV

    from tunes_player.core.playback.output_profile import PlaybackOutputProfile, PlaybackPathInfo

EngineCallback = Callable[[EngineEvent], None]

_LOG = logging.getLogger(__name__)
_SEEK_END_MARGIN_SEC = 1.0


def _position_poll_log_enabled() -> bool:
    return os.environ.get("TUNES_POSITION_POLL_LOG", "").lower() in (
        "1",
        "yes",
        "true",
    )


def _log_position_poll(label: str, position_sec: float, *, delta: float | None) -> None:
    if delta is None:
        msg = f"tunes position {label}: {position_sec:.3f}s"
    else:
        msg = f"tunes position {label}: {position_sec:.3f}s (delta={delta:+.3f})"
    print(msg, file=sys.stderr, flush=True)


def probe_playback_engine() -> str | None:
    """Return a user-facing error message if libmpv cannot be loaded, else None."""
    try:
        import mpv as mpv_module
    except OSError:
        return (
            "libmpv is not installed. Install the mpv package (e.g. apt install mpv libmpv2)."
        )
    except ImportError:
        return "python-mpv is not installed. Run: pip install python-mpv"
    try:
        player = mpv_module.MPV(video=False, vo="null", idle=True)
        player.terminate()
    except OSError:
        return (
            "libmpv is not installed. Install the mpv package (e.g. apt install mpv libmpv2)."
        )
    return None


class MpvEngine:
    """Headless in-process mpv instance for music playback."""

    def __init__(
        self,
        *,
        unity_gain: bool = False,
        volume: float = 0.72,
        audio_device: str | None = None,
        use_device_output: bool = False,
        output_profile: PlaybackOutputProfile | None = None,
        on_event: EngineCallback | None = None,
        endpoint_id: str | None = None,
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
        self._audio_device = audio_device
        self._use_device_output = use_device_output
        self._output_profile = output_profile
        self._software_volume = not unity_gain
        self._on_event = on_event
        self._endpoint_id = endpoint_id
        self._terminated = False
        self._shutting_down = False

        self._loaded_uri: str | None = None
        self._time_pos_lock = threading.Lock()
        self._time_pos_sec = 0.0
        self._duration_sec: float | None = None
        self._playing = False
        self._track_end_signaled = False
        self._last_position_update_at = 0.0
        self._last_position_poll_log_sec: float | None = None
        self._load_in_progress = False
        self._last_track_started_uri: str | None = None

        self._path_context: PlaybackPathContext | None = None
        self._path_info: PlaybackPathInfo | None = None
        self._negotiated_state = NegotiatedPlaybackState()
        self._direct_alsa_device_open = False
        self._opened_exclusive: bool | None = None
        self._last_output_format_key: tuple[object, ...] | None = None
        self._recovering_direct_alsa = False
        self._keep_alsa_open_on_track_change = self._usb_keep_device_open()

        options: dict[str, object] = {
            "video": False,
            "vo": "null",
            "keep_open": "always",
            "idle": True,
            "input_default_bindings": False,
            "input_vo_keyboard": False,
            "ytdl": False,
        }
        # Direct ALSA opens on first track load — not at idle init.
        if output_profile is not None and output_profile.direct_alsa:
            options.update({"ao": "null", "replaygain": "no"})
        else:
            options.update(base_audio_options(output_profile, use_device_output))
        if unity_gain:
            options["volume"] = 100
            options["replaygain"] = "no"
        else:
            options["volume"] = max(0.0, min(100.0, volume * 100.0))

        self._player: MPV = mpv_module.MPV(**options)
        self._configure_stream_demuxer()
        if audio_device and (output_profile is None or not output_profile.direct_alsa):
            self._player.audio_device = audio_device
        self._register_observers()

    def is_available(self) -> bool:
        return not self._terminated


    def set_event_callback(self, callback: EngineCallback | None) -> None:
        self._on_event = callback

    def set_playback_path_context(self, context: PlaybackPathContext | None) -> None:
        self._path_context = context

    def get_playback_path_info(self) -> PlaybackPathInfo | None:
        return self._path_info

    def refresh_playback_path_info(self) -> None:
        profile = self._output_profile
        context = self._path_context
        if profile is None or context is None:
            return
        self._refresh_negotiated_state()
        self._path_info = derive_playback_path_info(
            file_meta=context.file_meta,
            profile=profile,
            negotiated=self._negotiated_state,
            endpoint_id=context.endpoint_id,
            device_volume=context.device_volume,
            mpv_soft_volume=context.mpv_soft_volume,
        )

    def set_output_profile(self, profile: PlaybackOutputProfile | None) -> None:
        self._output_profile = profile
        if profile is None or not profile.direct_alsa:
            return
        if not self._direct_alsa_device_open:
            self._open_direct_alsa_device(profile)
            return
        if profile.use_exclusive != self._opened_exclusive:
            self._set_property("audio-exclusive", "yes" if profile.use_exclusive else "no")
            self._opened_exclusive = profile.use_exclusive

    def load(
        self,
        uri: str,
        *,
        start_sec: float = 0,
        output_profile: PlaybackOutputProfile | None = None,
        mode: str = "replace",
    ) -> None:
        if mode != "replace":
            raise NotImplementedError("in-process engine supports replace loads only")
        profile = output_profile if output_profile is not None else self._output_profile
        previous_uri = self._loaded_uri
        track_change = previous_uri is not None and previous_uri != uri

        format_key: tuple[object, ...] | None = None
        format_changed = False
        if profile is not None and profile.direct_alsa:
            format_key = self._output_format_key(profile)
            format_changed = bool(
                track_change
                and self._keep_alsa_open_on_track_change
                and self._last_output_format_key is not None
                and format_key != self._last_output_format_key
            )
            # Always reopen AO on track replace (#66). USB keep-open only
            # skips redundant format/buffer property churn, not ao-reload.
            if track_change and self._direct_alsa_device_open:
                self._reload_direct_alsa_output(stop_first=True)

        self._load_in_progress = True
        try:
            if profile is not None and profile.direct_alsa:
                self._apply_track_format(profile, format_key=format_key)
                self._last_output_format_key = format_key
                self.set_output_profile(profile)
            elif profile is not None:
                self._apply_track_format(profile)

            if previous_uri is None or format_changed or not self._keep_alsa_open_on_track_change:
                self._apply_buffer_policy(
                    uri,
                    profile,
                    warmup=previous_uri is None or format_changed,
                )

            self._loaded_uri = uri
            self._track_end_signaled = False
            self._seed_playback_position(max(0.0, start_sec))
            self._duration_sec = None
            self._last_position_update_at = time.monotonic()

            self._player.play(uri)
            if start_sec > 0:
                self._player.time_pos = start_sec
            self._player.pause = False
            self._playing = True
        finally:
            self._load_in_progress = False

        self.refresh_playback_path_info()
        self._emit("playback_path_changed")
        self._notify_track_started()
        self._emit("duration_changed")
        self._emit("playing_changed")

    def play(self) -> None:
        if self._loaded_uri is None:
            return
        self._player.pause = False
        self._playing = True
        self._touch_position_clock()
        self._emit("playing_changed")

    def pause(self) -> None:
        if self._loaded_uri is None:
            return
        self._player.pause = True
        self._playing = False
        self._publish_ui_position()
        self._emit("playing_changed")
        self._emit("position_changed")

    def stop(self) -> None:
        self._player.command("stop")
        self._track_end_signaled = False
        self._loaded_uri = None
        self._last_track_started_uri = None
        self._playing = False
        self._seed_playback_position(0.0)
        self._duration_sec = None
        self._emit("playing_changed")
        self._emit("position_changed")
        self._emit("duration_changed")

    def max_seek_position_sec(self) -> float | None:
        duration = self._duration_sec
        if duration is None or duration <= 0:
            return None
        return max(0.0, duration - _SEEK_END_MARGIN_SEC)

    def seek(self, position_sec: float, *, resume: bool | None = None) -> None:
        if self._loaded_uri is None:
            return
        target = max(0.0, position_sec)
        seek_cap = self.max_seek_position_sec()
        if seek_cap is not None:
            target = min(target, seek_cap)
        should_resume = self._playing if resume is None else resume
        self._player.time_pos = target
        self._seed_playback_position(target)
        if should_resume:
            self._player.pause = False
            self._playing = True
        self._emit("position_changed")

    def set_volume(self, level: float) -> None:
        self._volume = max(0.0, min(1.0, level))
        if not self._software_volume:
            return
        self._apply_software_volume()

    def set_bit_perfect(self, enabled: bool) -> None:
        new_software_volume = not enabled
        if self._unity_gain == enabled and self._software_volume == new_software_volume:
            return
        prev_software_volume = self._software_volume
        self._unity_gain = enabled
        self._software_volume = new_software_volume
        self._set_property("replaygain", "no")
        if enabled or not self._software_volume:
            self._set_property("volume", 100)
        else:
            self._apply_software_volume()
        if prev_software_volume != self._software_volume:
            self._sync_direct_alsa_buffer_policy_for_volume_mode()

    def get_position(self) -> float:
        """Return live mpv time-pos."""
        return self.query_time_pos()

    def _record_position_poll_log(self, label: str, position_sec: float) -> None:
        if not _position_poll_log_enabled():
            return
        previous = self._last_position_poll_log_sec
        delta = None if previous is None else position_sec - previous
        self._last_position_poll_log_sec = position_sec
        _log_position_poll(label, position_sec, delta=delta)

    def query_time_pos(self) -> float:
        """Return live mpv time-pos (call from the engine owner thread)."""
        if not self._shutting_down and not self._terminated:
            raw = self._get_property("time-pos")
            if raw is not None:
                try:
                    value = max(0.0, float(raw))
                except (TypeError, ValueError):
                    value = None
                if value is not None:
                    self._set_cached_time_pos(value)
                    self._record_position_poll_log("poll live", value)
                    return value
        with self._time_pos_lock:
            value = max(0.0, self._time_pos_sec)
        self._record_position_poll_log("poll cache", value)
        return value

    def get_time_pos(self) -> float:
        return self.query_time_pos()

    def _resume_position_sec(self) -> float:
        """Playback position for ALSA recovery seeks."""
        return self.query_time_pos()

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

    def snapshot_health_properties(self) -> dict[str, object]:
        """Read AO-related props for playback health diagnostics (#67).

        Call from the engine owner / GTK poll thread, not from arbitrary workers.
        """
        names = (
            "ao",
            "audio-device",
            "core-idle",
            "paused-for-cache",
            "mute",
        )
        return {name: self._get_property(name) for name in names}

    @property
    def load_in_progress(self) -> bool:
        return self._load_in_progress

    def release_alsa_device_contention(self) -> None:
        """No-op for in-process engine."""

    def recover_direct_alsa_output(
        self,
        *,
        full_reload: bool = False,
        ao_reload_only: bool = False,
    ) -> bool:
        profile = self._output_profile
        uri = self._loaded_uri
        if profile is None or not profile.direct_alsa or uri is None:
            return False
        if self._recovering_direct_alsa:
            return False
        resume_sec = self._resume_position_sec()
        # Near EOF: refuse AO recovery — PlayerService advances the queue (#66).
        if self._near_track_end(resume_sec):
            return False
        self._recovering_direct_alsa = True
        try:
            if ao_reload_only:
                self._reload_direct_alsa_output(stop_first=False)
            elif full_reload:
                self._reload_direct_alsa_output(stop_first=True)
                self._apply_buffer_policy(uri, profile, warmup=False)
                self._apply_track_format(profile)
                self._player.play(uri)
            elif resume_sec > 0.0:
                self._player.time_pos = resume_sec
                self._seed_playback_position(resume_sec)
            else:
                return self.recover_direct_alsa_output(ao_reload_only=True)
            self._player.pause = False
            self._playing = True
            self._touch_position_clock()
            self._emit("playing_changed")
            return True
        except Exception as exc:
            _LOG.warning("Direct ALSA playback recovery failed: %s", exc)
            return False
        finally:
            self._recovering_direct_alsa = False

    def quit(self) -> None:
        if self._terminated:
            return
        self._shutting_down = True
        self._terminated = True
        self._loaded_uri = None
        self._track_end_signaled = False
        self._playing = False
        self._player.terminate()

    def _configure_stream_demuxer(self) -> None:
        try:
            self._player._set_property(
                "stream-lavf-o",
                {"protocol_whitelist": "file,crypto,data,https,tcp,tls"},
            )
        except (TypeError, AttributeError):
            _LOG.warning("Could not set mpv stream-lavf-o for HTTPS streaming")

    def _open_direct_alsa_device(self, profile: PlaybackOutputProfile) -> None:
        if self._direct_alsa_device_open:
            return
        _LOG.info("Opening direct ALSA output for playback")
        for key, value in direct_alsa_engine_options(
            warmup=True,
            software_volume=self._software_volume,
        ).items():
            try:
                self._set_property(key, value)
            except Exception as exc:
                _LOG.warning("mpv rejected direct ALSA warmup property %s: %s", key, exc)
        self._set_property("ao", "alsa")
        self._negotiated_state = replace(self._negotiated_state, ao="alsa")
        if self._audio_device:
            self._set_property("audio-device", self._audio_device)
        self._set_property("audio-exclusive", "yes" if profile.use_exclusive else "no")
        try:
            self._player.command("ao-reload")
        except Exception as exc:
            _LOG.debug("mpv ao-reload after direct ALSA open failed: %s", exc)
        self._direct_alsa_device_open = True
        self._opened_exclusive = profile.use_exclusive

    def _apply_track_format(
        self,
        profile: PlaybackOutputProfile,
        *,
        format_key: tuple[object, ...] | None = None,
    ) -> None:
        if not profile.direct_alsa:
            return
        key = format_key if format_key is not None else self._output_format_key(profile)
        if (
            self._keep_alsa_open_on_track_change
            and self._last_output_format_key is not None
            and key == self._last_output_format_key
        ):
            return
        self._set_property("replaygain", "no")
        if self._unity_gain:
            self._set_property("volume", 100)
        else:
            self._apply_software_volume()
        try:
            self._set_property("alsa-resample", "yes" if profile.allow_resample else "no")
        except Exception as exc:
            _LOG.warning("mpv rejected alsa-resample: %s", exc)
        if profile.target_rate is not None:
            try:
                self._set_property("audio-samplerate", profile.target_rate)
            except Exception as exc:
                _LOG.warning("mpv rejected audio-samplerate %s: %s", profile.target_rate, exc)
        if profile.audio_format is not None:
            try:
                self._set_property("audio-format", profile.audio_format)
            except Exception as exc:
                _LOG.warning("mpv rejected audio-format %s: %s", profile.audio_format, exc)
        if profile.target_channels is not None and profile.target_channels != 2:
            try:
                self._set_property("audio-channels", profile.target_channels)
            except Exception as exc:
                _LOG.warning(
                    "mpv rejected audio-channels %s: %s",
                    profile.target_channels,
                    exc,
                )

    def _apply_buffer_policy(
        self,
        uri: str,
        profile: PlaybackOutputProfile | None,
        *,
        warmup: bool = True,
    ) -> None:
        input_class = classify_playback_uri(uri)
        direct_alsa = profile is not None and profile.direct_alsa
        options = mpv_options_for_input(
            input_class,
            direct_alsa=direct_alsa,
            warmup=warmup,
            software_volume=self._software_volume,
        )
        for key, value in options.items():
            try:
                self._set_property(key, value)
            except Exception as exc:
                _LOG.warning("mpv rejected buffer policy property %s: %s", key, exc)

    def _reload_direct_alsa_output(self, *, stop_first: bool) -> None:
        if stop_first:
            try:
                self._player.command("stop")
            except Exception as exc:
                _LOG.debug("mpv stop before ao reload failed: %s", exc)
        try:
            self._player.command("ao-reload")
        except Exception as exc:
            _LOG.debug("mpv ao-reload failed: %s", exc)

    def _sync_direct_alsa_buffer_policy_for_volume_mode(self) -> None:
        profile = getattr(self, "_output_profile", None)
        if profile is None or not profile.direct_alsa or not self._direct_alsa_device_open:
            return
        uri = self._loaded_uri or ""
        self._apply_buffer_policy(uri, profile, warmup=False)
        self._reload_direct_alsa_output(stop_first=False)

    @staticmethod
    def _output_format_key(profile: PlaybackOutputProfile) -> tuple[object, ...]:
        return (
            profile.target_rate,
            profile.audio_format,
            profile.target_channels,
            profile.allow_resample,
        )

    def _usb_keep_device_open(self) -> bool:
        try:
            from tunes_player.platform.linux.alsa_playback import usb_alsa_keep_device_open
            from tunes_player.platform.linux.alsa_xrun_monitor import parse_card_from_mpv_device

            card = parse_card_from_mpv_device(self._audio_device)
            endpoint_hint = f"alsa:hw:{card}:0" if card is not None else self._endpoint_id
            return usb_alsa_keep_device_open(endpoint_hint, self._audio_device)
        except ImportError:
            return bool(
                self._audio_device and "plughw" in self._audio_device.casefold()
            )

    def _set_property(self, name: str, value: object) -> None:
        flag = name.replace("_", "-")
        try:
            self._player._set_property(flag, value)
        except (TypeError, AttributeError):
            attr = name.replace("-", "_")
            setattr(self._player, attr, value)

    def _apply_software_volume(self) -> None:
        if not self._software_volume:
            return
        gain = max(0.0, min(1.0, self._volume))
        try:
            self._player.volume = gain * 100.0
        except (TypeError, AttributeError):
            self._set_property("volume", gain * 100.0)

    def _refresh_negotiated_state(self) -> None:
        self._negotiated_state = read_negotiated_playback_state(self._get_property)

    def _get_property(self, name: str) -> object:
        if self._shutting_down or self._terminated:
            return None
        flag = name.replace("_", "-")
        try:
            return self._player._get_property(flag)
        except (TypeError, AttributeError):
            attr = name.replace("-", "_")
            return getattr(self._player, attr, None)

    def _set_cached_time_pos(self, position_sec: float) -> None:
        with self._time_pos_lock:
            self._time_pos_sec = max(0.0, position_sec)

    def _cached_time_pos(self) -> float:
        with self._time_pos_lock:
            return self._time_pos_sec

    def _seed_playback_position(self, position_sec: float) -> None:
        position_sec = max(0.0, position_sec)
        self._set_cached_time_pos(position_sec)
        self._last_position_poll_log_sec = None
        self._touch_position_clock()

    def _snap_positions_to_track_end(self) -> None:
        """Align cached time-pos at demuxer EOF so queue advance can fire."""
        duration = self._duration_sec
        if duration is None or duration <= 0:
            return
        end_pos = min(max(self._cached_time_pos(), duration - 0.01), duration)
        self._seed_playback_position(end_pos)

    def _near_track_end(self, position_sec: float, *, margin: float = 3.0) -> bool:
        duration = self._duration_sec
        if duration is None or duration <= 0:
            return False
        return position_sec >= duration - margin

    def _publish_ui_position(self) -> None:
        if self._load_in_progress:
            return
        self._touch_position_clock()
        self._emit("position_changed")

    def _touch_position_clock(self) -> None:
        self._last_position_update_at = time.monotonic()

    def _notify_track_started(self) -> None:
        uri = self._loaded_uri
        if uri is None or uri == self._last_track_started_uri:
            return
        self._last_track_started_uri = uri
        self._track_end_signaled = False
        self._seed_playback_position(0.0)
        self._duration_sec = None
        self._emit("track_started")

    def _observe_path_property(self, prop_name: str) -> None:
        player = self._player

        @player.property_observer(prop_name)
        def _observer(_name: str, _value: object) -> None:
            if self._shutting_down or self._terminated or self._load_in_progress:
                return
            self._refresh_negotiated_state()
            self.refresh_playback_path_info()
            self._emit("playback_path_changed")

    def _register_observers(self) -> None:
        player = self._player

        @player.property_observer("time-pos")
        def _on_time_pos(_name: str, value: float | None) -> None:
            if (
                self._shutting_down
                or self._terminated
                or self._load_in_progress
                or value is None
            ):
                return
            position_sec = float(value)
            self._set_cached_time_pos(position_sec)
            if _position_poll_log_enabled():
                previous = self._last_position_poll_log_sec
                delta = None if previous is None else position_sec - previous
                _log_position_poll("observer", position_sec, delta=delta)
            self._publish_ui_position()

        @player.property_observer("duration")
        def _on_duration(_name: str, value: float | None) -> None:
            if (
                self._shutting_down
                or self._terminated
                or value is None
                or value <= 0
                or self._load_in_progress
            ):
                return
            self._duration_sec = float(value)
            self.refresh_playback_path_info()
            self._emit("duration_changed")
            self._emit("playback_path_changed")

        @player.property_observer("pause")
        def _on_pause(_name: str, value: bool | None) -> None:
            if self._shutting_down or self._terminated or self._load_in_progress:
                return
            self._playing = value is not True and self._loaded_uri is not None
            self._emit("playing_changed")

        for prop_name in (
            "ao",
            "audio-device",
            "audio-samplerate",
            "audio-format",
            "alsa-resample",
            "audio-channels",
        ):
            self._observe_path_property(prop_name)

        @player.event_callback("end-file")
        def _on_end_file(event: object) -> None:
            if self._shutting_down or self._terminated:
                return
            end_data = getattr(event, "data", None)
            if end_data is None:
                return
            reason = getattr(end_data, "reason", None)
            if end_file_triggers_playback_error(reason):
                self._playing = False
                self._emit("playback_error")
            elif end_file_triggers_track_finished(reason):
                self._snap_positions_to_track_end()
                self._playing = False
                self._emit("track_eof")
                self._emit("position_changed")
                self._emit("playing_changed")

    def _emit(self, event: EngineEvent) -> None:
        if self._shutting_down or self._terminated or self._on_event is None:
            return
        self._on_event(event)
