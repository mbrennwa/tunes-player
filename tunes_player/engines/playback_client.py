"""mpv playback via subprocess + JSON IPC."""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tunes_player.core.playback.engine import EngineEvent
from tunes_player.core.playback.mpv_cli import mpv_cli_args_from_options
from tunes_player.engines.playback_ipc import (
    end_file_triggers_playback_error,
    end_file_triggers_track_finished,
)

if TYPE_CHECKING:
    from tunes_player.core.playback.output_profile import PlaybackOutputProfile

EngineCallback = Callable[[EngineEvent], None]

_LOG = logging.getLogger(__name__)
_CONNECT_TIMEOUT_SEC = 5.0
_COMMAND_TIMEOUT_SEC = 30.0
_POSITION_INTERVAL_SEC = 0.1


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


class MpvPlaybackClient:
    """Headless mpv child process for music playback."""

    def __init__(
        self,
        *,
        unity_gain: bool = False,
        volume: float = 0.72,
        audio_device: str | None = None,
        use_device_output: bool = False,
        output_profile: PlaybackOutputProfile | None = None,
        on_event: EngineCallback | None = None,
        ipc_socket_path: Path | None = None,
        bit_perfect: bool | None = None,
    ) -> None:
        if bit_perfect is not None:
            unity_gain = bit_perfect

        mpv_bin = shutil.which("mpv")
        if mpv_bin is None:
            raise RuntimeError(
                "mpv is not installed. Install the mpv package (e.g. apt install mpv)."
            )

        self._mpv_bin = mpv_bin
        self._unity_gain = unity_gain
        self._volume = volume
        self._audio_device = audio_device
        self._use_device_output = use_device_output
        self._output_profile = output_profile
        self._software_volume = not unity_gain and not use_device_output
        self._on_event = on_event
        self._socket_path = ipc_socket_path or Path(f"/tmp/tunes-mpv-{time.time_ns()}.sock")

        self._proc: subprocess.Popen[bytes] | None = None
        self._sock: socket.socket | None = None
        self._sock_file: Any = None
        self._reader: threading.Thread | None = None
        self._running = False
        self._request_id = 0
        self._responses: dict[int, dict[str, Any]] = {}
        self._response_lock = threading.Lock()
        self._response_ready = threading.Condition(self._response_lock)
        self._command_lock = threading.Lock()

        self._loaded_uri: str | None = None
        self._position_sec = 0.0
        self._duration_sec: float | None = None
        self._playing = False
        self._track_end_signaled = False
        self._last_position_emit = 0.0
        self._shutdown = False

        self._start_process()
        self._configure_stream_demuxer()
        self._observe_properties()

    def _build_startup_args(self) -> list[str]:
        profile = self._output_profile
        args = [
            "--idle=yes",
            "--keep-open=yes",
            f"--input-ipc-server={self._socket_path}",
            "--input-default-bindings=no",
            "--input-vo-keyboard=no",
            "--no-video",
            "--vo=null",
            "--ytdl=no",
        ]
        args.extend(
            mpv_cli_args_from_options(_base_audio_options(profile, self._use_device_output))
        )
        if self._unity_gain:
            args.append("--volume=100")
            args.append("--replaygain=no")
        else:
            args.append(f"--volume={max(0.0, min(100.0, self._volume * 100.0))}")
        if self._audio_device:
            args.append(f"--audio-device={self._audio_device}")
        if profile is not None and profile.direct_alsa and profile.use_exclusive:
            args.append("--audio-exclusive=yes")
        return args

    def _start_process(self) -> None:
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._terminate_stale_mpv_instances()
        if self._socket_path.exists():
            self._socket_path.unlink()

        cmd = [self._mpv_bin, *self._build_startup_args()]
        _LOG.info("Starting subprocess mpv: %s", " ".join(cmd))
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._connect_socket()
        self._running = True
        self._reader = threading.Thread(
            target=self._read_loop,
            name="mpv-ipc-reader",
            daemon=True,
        )
        self._reader.start()
        self.command("enable_event", "end-file", 1)

    def _connect_socket(self) -> None:
        deadline = time.monotonic() + _CONNECT_TIMEOUT_SEC
        while time.monotonic() < deadline:
            if self._socket_path.exists():
                break
            if self._proc is not None and self._proc.poll() is not None:
                raise RuntimeError("mpv subprocess exited during startup")
            time.sleep(0.05)
        else:
            raise RuntimeError("Timed out waiting for mpv IPC socket")

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(str(self._socket_path))
        self._sock = sock
        self._sock_file = sock.makefile("rwb")

    def _configure_stream_demuxer(self) -> None:
        try:
            self.set_property(
                "stream-lavf-o",
                {"protocol_whitelist": "file,crypto,data,https,tcp,tls"},
            )
        except (TimeoutError, OSError, RuntimeError):
            _LOG.warning("Could not set mpv stream-lavf-o for HTTPS streaming")

    def _observe_properties(self) -> None:
        self.command("observe_property", 1, "time-pos")
        self.command("observe_property", 2, "duration")
        self.command("observe_property", 3, "pause")

    def _read_loop(self) -> None:
        assert self._sock_file is not None
        while self._running:
            line = self._sock_file.readline()
            if not line:
                break
            try:
                message = json.loads(line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            request_id = message.get("request_id")
            if request_id is not None:
                with self._response_ready:
                    self._responses[int(request_id)] = message
                    self._response_ready.notify_all()
                continue
            event = message.get("event")
            if event == "property-change":
                self._handle_property_change(message.get("name"), message.get("data"))
            elif event == "end-file":
                self._handle_end_file(
                    message.get("reason"),
                    file_error=message.get("file_error"),
                )

    def _terminate_stale_mpv_instances(self) -> None:
        pattern = f"input-ipc-server={self._socket_path}"
        try:
            result = subprocess.run(
                ["pgrep", "-f", pattern],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return
        pids = [int(pid) for pid in result.stdout.split() if pid.strip().isdigit()]
        if not pids:
            return
        for pid in pids:
            _LOG.warning("Terminating stale mpv process %s for %s", pid, self._socket_path)
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
        time.sleep(0.2)

    def _handle_property_change(self, name: str | None, data: object) -> None:
        if name == "time-pos":
            if data is None:
                return
            try:
                pos = float(data)
            except (TypeError, ValueError):
                return
            if pos < 0:
                return
            self._position_sec = pos
            now = time.monotonic()
            if now - self._last_position_emit >= _POSITION_INTERVAL_SEC:
                self._last_position_emit = now
                self._emit("position_changed")
        elif name == "duration":
            if data is None:
                return
            try:
                duration = float(data)
            except (TypeError, ValueError):
                return
            if duration <= 0:
                return
            self._duration_sec = duration
            self._emit("duration_changed")
        elif name == "pause":
            paused = data is True or data == "yes"
            self._playing = not paused and self._loaded_uri is not None
            self._emit("playing_changed")

    def _handle_end_file(self, reason: object, *, file_error: object = None) -> None:
        if end_file_triggers_track_finished(reason):
            self._signal_track_finished()
        elif end_file_triggers_playback_error(reason):
            detail = f": {file_error}" if file_error else ""
            _LOG.warning("mpv end-file reason=%s%s", reason, detail)
            self._playing = False
            self._loaded_uri = None
            self._emit("playback_error")

    def command(self, *args: object, timeout: float = _COMMAND_TIMEOUT_SEC) -> dict[str, Any]:
        if self._sock_file is None:
            raise OSError("mpv IPC connection closed")
        with self._command_lock:
            self._request_id += 1
            request_id = self._request_id
            payload = json.dumps(
                {"command": list(args), "request_id": request_id},
                separators=(",", ":"),
            ).encode("utf-8") + b"\n"
            self._sock_file.write(payload)
            self._sock_file.flush()
            deadline = time.monotonic() + timeout
            with self._response_ready:
                while request_id not in self._responses:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"mpv IPC command timed out: {args!r}")
                    self._response_ready.wait(timeout=remaining)
                return self._responses.pop(request_id)

    def set_property(self, name: str, value: object) -> None:
        flag = name.replace("_", "-")
        response = self.command("set_property", flag, value)
        if response.get("error") != "success":
            raise RuntimeError(f"mpv rejected property {flag}: {response.get('error')}")

    def get_property(self, name: str) -> object:
        flag = name.replace("_", "-")
        response = self.command("get_property", flag)
        if response.get("error") != "success":
            return None
        return response.get("data")

    def set_audio_device(self, audio_device: str | None) -> None:
        if audio_device:
            self.set_property("audio-device", audio_device)

    def set_output_profile(self, profile: PlaybackOutputProfile | None) -> None:
        self._output_profile = profile
        if profile is None or not profile.direct_alsa:
            return
        self.set_property("ao", "alsa")
        self.set_property("audio-exclusive", "yes" if profile.use_exclusive else "no")

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
        response = self.command("loadfile", uri, "replace")
        if response.get("error") != "success":
            raise RuntimeError(f"mpv loadfile failed: {response.get('error')}")
        if start_sec > 0:
            self.command("seek", start_sec, "absolute")
        self.set_property("pause", False)
        self._playing = True
        self._emit("duration_changed")
        self._emit("playing_changed")

    def _apply_track_format(self, profile: PlaybackOutputProfile) -> None:
        if not profile.direct_alsa:
            return
        self.set_property("replaygain", "no")
        if self._unity_gain:
            self.set_property("volume", 100)
        else:
            self._apply_software_volume()
        try:
            self.set_property("alsa-resample", "yes" if profile.allow_resample else "no")
        except RuntimeError as exc:
            _LOG.warning("mpv rejected alsa-resample: %s", exc)
        if profile.target_rate is not None:
            try:
                self.set_property("audio-samplerate", profile.target_rate)
            except RuntimeError as exc:
                _LOG.warning("mpv rejected audio-samplerate %s: %s", profile.target_rate, exc)
        if profile.audio_format is not None:
            try:
                self.set_property("audio-format", profile.audio_format)
            except RuntimeError as exc:
                _LOG.warning("mpv rejected audio-format %s: %s", profile.audio_format, exc)
        if profile.target_channels is not None and profile.target_channels != 2:
            try:
                self.set_property("audio-channels", profile.target_channels)
            except RuntimeError as exc:
                _LOG.warning(
                    "mpv rejected audio-channels %s: %s",
                    profile.target_channels,
                    exc,
                )

    def play(self) -> None:
        if self._loaded_uri is None:
            return
        self.set_property("pause", False)
        self._playing = True
        self._emit("playing_changed")

    def pause(self) -> None:
        if self._loaded_uri is None:
            return
        self.set_property("pause", True)
        pos = self.get_property("time-pos")
        self._playing = False
        if pos is not None:
            self._position_sec = float(pos)
        self._emit("playing_changed")
        self._emit("position_changed")

    def stop(self) -> None:
        self.command("stop")
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
        self.command("seek", target, "absolute")
        self._position_sec = target
        self._emit("position_changed")

    def set_volume(self, level: float) -> None:
        self._volume = max(0.0, min(1.0, level))
        if self._unity_gain:
            return
        self._apply_software_volume()

    def set_bit_perfect(self, enabled: bool) -> None:
        self._unity_gain = enabled
        self._software_volume = not enabled and not self._use_device_output
        self.set_property("replaygain", "no")
        if enabled or not self._software_volume:
            self.set_property("volume", 100)
        else:
            self._apply_software_volume()

    def _apply_software_volume(self) -> None:
        if not self._software_volume:
            return
        gain = max(0.0, min(1.0, self._volume))
        self.set_property("volume", gain * 100.0)

    def get_position(self) -> float:
        if self._shutdown or self._sock_file is None:
            return self._position_sec
        try:
            pos = self.get_property("time-pos")
        except OSError:
            return self._position_sec
        if pos is not None:
            try:
                self._position_sec = float(pos)
            except (TypeError, ValueError):
                pass
        return self._position_sec

    def get_duration(self) -> float | None:
        if self._shutdown or self._sock_file is None:
            return self._duration_sec
        try:
            duration = self.get_property("duration")
        except OSError:
            return self._duration_sec
        if duration is not None:
            try:
                value = float(duration)
            except (TypeError, ValueError):
                return self._duration_sec
            if value > 0:
                self._duration_sec = value
        return self._duration_sec

    def is_playing(self) -> bool:
        if self._loaded_uri is None or self._shutdown or self._sock_file is None:
            return False
        try:
            paused = self.get_property("pause")
        except OSError:
            return self._playing
        if paused is None:
            return self._playing
        return paused is not True and paused != "yes"

    def quit(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self._loaded_uri = None
        self._track_end_signaled = False
        self._playing = False
        self._running = False
        if self._sock_file is not None and self._proc is not None and self._proc.poll() is None:
            try:
                self.command("quit", timeout=1.0)
            except (TimeoutError, OSError):
                pass
        if self._sock_file is not None:
            try:
                self._sock_file.close()
            except OSError:
                pass
            self._sock_file = None
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._proc is not None:
            if self._proc.poll() is None:
                try:
                    self._proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self._proc.terminate()
                    try:
                        self._proc.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        self._proc.kill()
            self._proc = None
        if self._socket_path.exists():
            try:
                self._socket_path.unlink()
            except OSError:
                pass

    def _signal_track_finished(self) -> None:
        if self._loaded_uri is None or self._track_end_signaled:
            return
        self._track_end_signaled = True
        self._playing = False
        self._emit("track_finished")

    def _emit(self, event: EngineEvent) -> None:
        if self._on_event is not None:
            self._on_event(event)
