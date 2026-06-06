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
import atexit
import weakref
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tunes_player.core.playback.engine import EngineEvent
from tunes_player.core.playback.buffer_policy import (
    classify_playback_uri,
    direct_alsa_engine_options,
    log_buffer_policy,
    mpv_options_for_input,
)
from tunes_player.core.playback.mpv_cli import base_audio_options, mpv_cli_args_from_options
from tunes_player.core.playback.playback_path import (
    NegotiatedPlaybackState,
    PlaybackPathContext,
    derive_playback_path_info,
)
from tunes_player.engines.playback_ipc import (
    end_file_applies_to_playlist_entry,
    end_file_triggers_playback_error,
    end_file_triggers_track_finished,
)

if TYPE_CHECKING:
    from tunes_player.core.playback.output_profile import PlaybackOutputProfile, PlaybackPathInfo
    from tunes_player.core.playback.playback_path import PlaybackPathContext

EngineCallback = Callable[[EngineEvent], None]

_LOG = logging.getLogger(__name__)
_CONNECT_TIMEOUT_SEC = 5.0
_COMMAND_TIMEOUT_SEC = 5.0
_POSITION_INTERVAL_SEC = 0.25
_LIVE_CLIENTS: weakref.WeakSet = weakref.WeakSet()


def _descendant_pids(root_pid: int | None = None) -> set[int]:
    """Return child process IDs rooted at *root_pid* (default: current process)."""
    descendants: set[int] = set()
    stack = [root_pid if root_pid is not None else os.getpid()]
    while stack:
        pid = stack.pop()
        try:
            with open(f"/proc/{pid}/task/{pid}/children", encoding="ascii") as handle:
                for token in handle.read().split():
                    if token.isdigit():
                        child = int(token)
                        descendants.add(child)
                        stack.append(child)
        except OSError:
            continue
    return descendants


def _configure_mpv_child_process() -> None:
    """Send SIGTERM to mpv when the Tunes parent process exits."""
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        if libc.prctl(1, signal.SIGTERM) != 0:  # PR_SET_PDEATHSIG
            return
    except (AttributeError, OSError):
        return


@atexit.register
def _quit_live_mpv_clients() -> None:
    for client in list(_LIVE_CLIENTS):
        try:
            client.quit()
        except Exception:
            _LOG.exception("Failed to quit mpv playback client during process exit")


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
        endpoint_id: str | None = None,
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
        self._endpoint_id = endpoint_id
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
        self._last_position_update_at = 0.0
        self._shutdown = False
        self._path_context: PlaybackPathContext | None = None
        self._path_info: PlaybackPathInfo | None = None
        self._negotiated_state = NegotiatedPlaybackState()
        self._active_playlist_entry_id: int | None = None
        self._load_in_progress = False
        self._recovering_direct_alsa = False
        self._stable_output_active = False
        self._last_output_format_key: tuple[object, ...] | None = None
        self._direct_alsa_device_open = False
        self._opened_exclusive: bool | None = None
        self._keep_alsa_open_on_track_change = self._usb_keep_device_open()
        self._used_chrt = False

        _LIVE_CLIENTS.add(self)
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
        if profile is not None and profile.direct_alsa:
            # Keep idle mpv on the null AO until the first track load opens ALSA.
            # Opening an exclusive USB DAC at prewarm caused the subprocess to exit
            # while the GUI still held a stale IPC client.
            args.extend(mpv_cli_args_from_options({"ao": "null", "replaygain": "no"}))
        else:
            args.extend(
                mpv_cli_args_from_options(
                    base_audio_options(profile, self._use_device_output)
                )
            )
        if self._unity_gain:
            args.append("--volume=100")
        else:
            args.append(f"--volume={max(0.0, min(100.0, self._volume * 100.0))}")
        if self._audio_device and (profile is None or not profile.direct_alsa):
            args.append(f"--audio-device={self._audio_device}")
        return args

    def _start_process(self) -> None:
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        protected = _descendant_pids()
        self._terminate_stale_mpv_instances(protected_pids=protected)
        if self._socket_path.exists():
            self._socket_path.unlink(missing_ok=True)

        from tunes_player.platform.linux.playback_priority import mpv_subprocess_command

        mpv_args = self._build_startup_args()
        cmd, self._used_chrt = mpv_subprocess_command(self._mpv_bin, mpv_args)
        _LOG.info("Starting subprocess mpv: %s", " ".join(cmd))
        popen_kwargs: dict[str, object] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.PIPE,
        }
        if os.name == "posix":
            popen_kwargs["preexec_fn"] = _configure_mpv_child_process
        self._proc = subprocess.Popen(cmd, **popen_kwargs)  # type: ignore[arg-type]
        protected.add(self._proc.pid)
        self.release_alsa_device_contention()
        stderr = self._proc.stderr
        if stderr is not None:
            threading.Thread(
                target=self._drain_mpv_stderr,
                args=(stderr,),
                name="mpv-stderr",
                daemon=True,
            ).start()
        self._raise_child_playback_priority()
        try:
            self._connect_socket()
        except Exception:
            if self._proc is not None and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=1.0)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        self._proc.kill()
                    except OSError:
                        pass
            self._proc = None
            raise
        self._running = True
        self._reader = threading.Thread(
            target=self._read_loop,
            name="mpv-ipc-reader",
            daemon=True,
        )
        self._reader.start()
        self.command("enable_event", "end-file", 1)

    def _drain_mpv_stderr(self, stderr: Any) -> None:
        try:
            for raw_line in stderr:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                if line:
                    _LOG.warning("mpv: %s", line)
        except OSError:
            pass
        finally:
            try:
                stderr.close()
            except OSError:
                pass

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
        self.command("observe_property", 4, "ao")
        self.command("observe_property", 5, "audio-device")
        self.command("observe_property", 6, "audio-samplerate")
        self.command("observe_property", 7, "audio-format")
        self.command("observe_property", 8, "alsa-resample")
        self.command("observe_property", 9, "audio-channels")

    def _read_loop(self) -> None:
        assert self._sock_file is not None
        while self._running:
            try:
                line = self._sock_file.readline()
            except OSError:
                self._mark_ipc_disconnected()
                break
            if not line:
                self._mark_ipc_disconnected()
                break
            try:
                message = json.loads(line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            try:
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
                        playlist_entry_id=message.get("playlist_entry_id"),
                    )
            except Exception:
                _LOG.exception("Unhandled mpv IPC event")
        if not self._shutdown:
            proc = self._proc
            returncode = proc.poll() if proc is not None else None
            if returncode is not None:
                _LOG.error(
                    "mpv IPC reader exited unexpectedly (returncode=%s)",
                    returncode,
                )
            else:
                _LOG.warning("mpv IPC socket closed before subprocess exit")

    def _terminate_stale_mpv_instances(
        self,
        *,
        protected_pids: set[int] | None = None,
    ) -> None:
        protected = protected_pids or set()
        self._terminate_mpv_matching(
            f"input-ipc-server={self._socket_path}",
            exclude_pids=protected,
        )

    def release_alsa_device_contention(self) -> None:
        """Drop orphan mpv processes that still hold the configured ALSA device."""
        if not self._audio_device:
            return
        from tunes_player.platform.linux.mpv_cleanup import terminate_mpv_using_audio_device

        own_pid = self._proc.pid if self._proc is not None else None
        terminate_mpv_using_audio_device(self._audio_device, exclude_pid=own_pid)

    def _terminate_mpv_matching(
        self,
        pattern: str,
        *,
        exclude_pid: int | None = None,
        exclude_pids: set[int] | None = None,
    ) -> None:
        protected = set(exclude_pids or ())
        if exclude_pid is not None:
            protected.add(exclude_pid)
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
            if pid in protected:
                continue
            _LOG.warning("Terminating stale mpv process %s matching %s", pid, pattern)
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
        time.sleep(0.2)

    def _handle_property_change(self, name: str | None, data: object) -> None:
        if name in {
            "ao",
            "audio-device",
            "audio-samplerate",
            "audio-format",
            "alsa-resample",
            "audio-channels",
        }:
            self._update_negotiated_property(name, data)
            if not self._load_in_progress:
                self.refresh_playback_path_info()
                self._emit("playback_path_changed")
            return

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
            self._last_position_update_at = now
            if self._load_in_progress:
                return
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
            if self._load_in_progress:
                return
            self._emit("duration_changed")
            self.refresh_playback_path_info()
            self._emit("playback_path_changed")
        elif name == "pause":
            if self._load_in_progress:
                return
            paused = data is True or data == "yes"
            self._playing = not paused and self._loaded_uri is not None
            self._emit("playing_changed")

    def _handle_end_file(
        self,
        reason: object,
        *,
        file_error: object = None,
        playlist_entry_id: object = None,
    ) -> None:
        if not end_file_applies_to_playlist_entry(
            active_entry_id=self._active_playlist_entry_id,
            event_entry_id=playlist_entry_id,
        ):
            return
        if end_file_triggers_track_finished(reason):
            self._signal_track_finished()
        elif end_file_triggers_playback_error(reason):
            detail = f": {file_error}" if file_error else ""
            _LOG.warning("mpv end-file reason=%s%s", reason, detail)
            self._playing = False
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
            try:
                self._sock_file.write(payload)
                self._sock_file.flush()
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                self._mark_ipc_disconnected()
                raise OSError("mpv IPC connection closed") from exc
            deadline = time.monotonic() + timeout
            with self._response_ready:
                while request_id not in self._responses:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"mpv IPC command timed out: {args!r}")
                    self._response_ready.wait(timeout=remaining)
                return self._responses.pop(request_id)

    def is_available(self) -> bool:
        """Return True while the mpv subprocess and IPC socket are usable."""
        if self._shutdown or not self._running:
            return False
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return False
        return self._sock_file is not None

    def _mark_ipc_disconnected(self) -> None:
        self._running = False
        self._playing = False
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
        if self._direct_alsa_device_open and not self._ao_is_alsa(
            self.get_property("ao")
        ):
            _LOG.warning("mpv ao is not alsa; reopening direct ALSA output")
            self._direct_alsa_device_open = False
        if not self._direct_alsa_device_open:
            self._open_direct_alsa_device(profile)
            return
        if profile.use_exclusive != self._opened_exclusive:
            self.set_property(
                "audio-exclusive",
                "yes" if profile.use_exclusive else "no",
            )
            self._opened_exclusive = profile.use_exclusive

    def _open_direct_alsa_device(self, profile: PlaybackOutputProfile) -> None:
        if self._direct_alsa_device_open:
            return
        self.release_alsa_device_contention()
        _LOG.info("Opening direct ALSA output for playback")
        for key, value in direct_alsa_engine_options(warmup=True).items():
            try:
                self.set_property(key, value)
            except RuntimeError as exc:
                _LOG.warning("mpv rejected direct ALSA warmup property %s: %s", key, exc)
        self.set_property("ao", "alsa")
        if self._audio_device:
            self.set_property("audio-device", self._audio_device)
        self.set_property(
            "audio-exclusive",
            "yes" if profile.use_exclusive else "no",
        )
        try:
            self.command("ao-reload")
        except (TimeoutError, OSError) as exc:
            _LOG.debug("mpv ao-reload after direct ALSA open failed: %s", exc)
        self._direct_alsa_device_open = True
        self._opened_exclusive = profile.use_exclusive
        ao = self.get_property("ao")
        if not self._ao_is_alsa(ao):
            _LOG.warning("mpv ao is still %s after opening direct ALSA", ao)

    def ping(self) -> None:
        """Verify the mpv IPC connection is alive."""
        self.get_property("idle-active")

    def set_playback_path_context(self, context: PlaybackPathContext | None) -> None:
        self._path_context = context

    def get_playback_path_info(self) -> PlaybackPathInfo | None:
        return self._path_info

    def refresh_playback_path_info(self) -> None:
        """Update cached path info from observed mpv properties (no blocking IPC)."""
        profile = self._output_profile
        context = self._path_context
        if profile is None or context is None:
            return
        self._path_info = derive_playback_path_info(
            file_meta=context.file_meta,
            profile=profile,
            negotiated=self._negotiated_state,
            endpoint_id=context.endpoint_id,
            device_volume=context.device_volume,
            mpv_soft_volume=context.mpv_soft_volume,
        )

    def _update_negotiated_property(self, name: str, data: object) -> None:
        if name == "ao":
            self._negotiated_state = replace(
                self._negotiated_state,
                ao=self._coerce_negotiated_text(data),
            )
        elif name == "audio-device":
            self._negotiated_state = replace(
                self._negotiated_state,
                audio_device=self._coerce_negotiated_text(data),
            )
        elif name == "audio-samplerate":
            self._negotiated_state = replace(
                self._negotiated_state,
                audio_samplerate=self._coerce_negotiated_int(data),
            )
        elif name == "audio-format":
            self._negotiated_state = replace(
                self._negotiated_state,
                audio_format=self._coerce_negotiated_text(data),
            )
        elif name == "alsa-resample":
            self._negotiated_state = replace(
                self._negotiated_state,
                alsa_resample=self._coerce_negotiated_bool(data),
            )
        elif name == "audio-channels":
            self._negotiated_state = replace(
                self._negotiated_state,
                audio_channels=self._coerce_negotiated_int(data),
            )

    @staticmethod
    def _ao_is_alsa(value: object) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return value.casefold() == "alsa"
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    name = item.get("name")
                    if isinstance(name, str) and name.casefold() == "alsa":
                        enabled = item.get("enabled")
                        if enabled is None or enabled is True:
                            return True
            return False
        return str(value).casefold() == "alsa"

    @staticmethod
    def _coerce_negotiated_text(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _coerce_negotiated_int(value: object) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_negotiated_bool(value: object) -> bool | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            lowered = value.casefold()
            if lowered in ("yes", "true", "1", "on"):
                return True
            if lowered in ("no", "false", "0", "off"):
                return False
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
        previous_uri = self._loaded_uri
        track_change = previous_uri is not None and previous_uri != uri
        if (
            track_change
            and profile is not None
            and profile.direct_alsa
            and not self._keep_alsa_open_on_track_change
        ):
            self._reset_direct_alsa_for_track_change()

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
            if format_changed:
                _LOG.info(
                    "USB output format changed %s -> %s; reloading ALSA",
                    self._last_output_format_key,
                    format_key,
                )
                self._reload_direct_alsa_output(stop_first=True)

        self._load_in_progress = True
        self._active_playlist_entry_id = None
        try:
            if profile is not None and profile.direct_alsa:
                self.set_output_profile(profile)
                self._apply_track_format(profile, format_key=format_key)
                self._last_output_format_key = format_key
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
            self._position_sec = max(0.0, start_sec)
            self._duration_sec = None
            self._last_position_emit = 0.0
            self._last_position_update_at = time.monotonic()
            response = self.command("loadfile", uri, "replace")
            if response.get("error") != "success":
                raise RuntimeError(f"mpv loadfile failed: {response.get('error')}")
            data = response.get("data")
            if isinstance(data, dict):
                entry_id = data.get("playlist_entry_id")
                if entry_id is not None:
                    self._active_playlist_entry_id = int(entry_id)
            if start_sec > 0:
                self.command("seek", start_sec, "absolute")
            self.set_property("pause", False)
            self._playing = True
        finally:
            self._load_in_progress = False
        self.refresh_playback_path_info()
        self._emit("playback_path_changed")
        self._emit("duration_changed")
        self._emit("playing_changed")

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

    @staticmethod
    def _output_format_key(profile: PlaybackOutputProfile) -> tuple[object, ...]:
        return (
            profile.target_rate,
            profile.audio_format,
            profile.target_channels,
            profile.allow_resample,
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
        )
        log_buffer_policy(input_class, uri, options)
        for key, value in options.items():
            try:
                self.set_property(key, value)
            except RuntimeError as exc:
                _LOG.warning("mpv rejected buffer policy property %s: %s", key, exc)

    def _reload_direct_alsa_output(self, *, stop_first: bool) -> None:
        if stop_first:
            try:
                self.command("stop")
            except (TimeoutError, OSError) as exc:
                _LOG.debug("mpv stop before ao reload failed: %s", exc)
        try:
            self.command("ao-reload")
        except (TimeoutError, OSError) as exc:
            _LOG.debug("mpv ao-reload failed: %s", exc)

    def _reset_direct_alsa_for_track_change(self) -> None:
        _LOG.info("Resetting direct ALSA output before track change")
        self._reload_direct_alsa_output(stop_first=True)

    def refresh_usb_playback_isolation(self) -> None:
        if self._proc is None or self._proc.pid <= 0:
            return
        try:
            from tunes_player.platform.linux.alsa_xrun_monitor import parse_card_from_mpv_device
            from tunes_player.platform.linux.playback_priority import refresh_usb_mpv_affinity

            card = parse_card_from_mpv_device(self._audio_device)
            if card is not None:
                refresh_usb_mpv_affinity(self._proc.pid, card)
        except ImportError:
            pass

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
        resume_sec = self.get_position()
        preview = uri if len(uri) <= 120 else f"{uri[:117]}..."
        self._recovering_direct_alsa = True
        try:
            if ao_reload_only:
                _LOG.warning(
                    "ao-reload direct ALSA recovery for %s at %.2fs",
                    preview,
                    resume_sec,
                )
                self._reload_direct_alsa_output(stop_first=False)
                if resume_sec > 0.0:
                    self.command("seek", resume_sec, "absolute")
                    self._position_sec = resume_sec
                self._touch_position_clock()
                self.set_property("pause", False)
                self._playing = True
                self._emit("playing_changed")
                return True

            if not full_reload:
                _LOG.warning("Light direct ALSA recovery for %s at %.2fs", preview, resume_sec)
                if resume_sec > 0.0:
                    self.command("seek", resume_sec, "absolute")
                    self._position_sec = resume_sec
                self._touch_position_clock()
                self.set_property("pause", False)
                self._playing = True
                self._emit("playing_changed")
                return True

            _LOG.warning("Full direct ALSA recovery for %s at %.2fs", preview, resume_sec)
            self._reload_direct_alsa_output(stop_first=True)
            self._track_end_signaled = False
            self._apply_buffer_policy(uri, profile, warmup=False)
            self._apply_track_format(profile)
            self.command("loadfile", uri, "replace")
            if resume_sec > 0.5:
                self.command("seek", resume_sec, "absolute")
                self._position_sec = resume_sec
            self._touch_position_clock()
            self.set_property("pause", False)
            self._playing = True
            self._emit("playing_changed")
            return True
        except (TimeoutError, OSError, RuntimeError) as exc:
            _LOG.warning("Direct ALSA playback recovery failed: %s", exc)
            return False
        finally:
            self._recovering_direct_alsa = False

    def switch_to_stable_alsa_output(self) -> bool:
        if self._stable_output_active:
            return False
        try:
            from tunes_player.platform.linux.alsa_playback import plughw_mpv_device
        except ImportError:
            return False
        stable_device = plughw_mpv_device(self._audio_device)
        profile = self._output_profile
        uri = self._loaded_uri
        if stable_device is None or profile is None or uri is None or not profile.direct_alsa:
            return False
        resume_sec = self.get_position()
        preview = uri if len(uri) <= 120 else f"{uri[:117]}..."
        _LOG.warning(
            "Switching USB playback to plughw/non-exclusive ALSA (%s) at %.2fs",
            stable_device,
            resume_sec,
        )
        self._stable_output_active = True
        self._audio_device = stable_device
        self._recovering_direct_alsa = True
        try:
            self.set_property("audio-exclusive", "no")
            self.set_property("audio-device", stable_device)
            self._reload_direct_alsa_output(stop_first=True)
            self._track_end_signaled = False
            self._apply_buffer_policy(uri, profile, warmup=False)
            self._apply_track_format(profile)
            self.command("loadfile", uri, "replace")
            if resume_sec > 0.5:
                self.command("seek", resume_sec, "absolute")
                self._position_sec = resume_sec
            self._touch_position_clock()
            self.set_property("pause", False)
            self._playing = True
            self._emit("playing_changed")
            return True
        except (TimeoutError, OSError, RuntimeError) as exc:
            _LOG.warning("Stable ALSA fallback failed: %s", exc)
            self._stable_output_active = False
            return False
        finally:
            self._recovering_direct_alsa = False

    def _raise_child_playback_priority(self) -> None:
        if self._proc is None or self._proc.pid <= 0:
            return
        try:
            from tunes_player.platform.linux.alsa_xrun_monitor import parse_card_from_mpv_device
            from tunes_player.platform.linux.playback_priority import pin_mpv_subprocess

            card = parse_card_from_mpv_device(self._audio_device)
            pin_mpv_subprocess(self._proc.pid, alsa_card=card, used_chrt=self._used_chrt)
        except ImportError:
            pass

    def play(self) -> None:
        if self._loaded_uri is None:
            return
        self.set_property("pause", False)
        self._playing = True
        self._touch_position_clock()
        self._emit("playing_changed")

    def pause(self) -> None:
        if self._loaded_uri is None:
            return
        self.set_property("pause", True)
        self._playing = False
        self._emit("playing_changed")
        self._emit("position_changed")

    def stop(self) -> None:
        self.command("stop")
        self._track_end_signaled = False
        self._loaded_uri = None
        self._active_playlist_entry_id = None
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
        self._touch_position_clock()
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
        """Return the last mpv time-pos observed on the IPC reader thread."""
        return self._position_sec

    @property
    def load_in_progress(self) -> bool:
        return self._load_in_progress

    def playback_stall_age_sec(self) -> float | None:
        """Seconds since the last time-pos update while playback is intended."""
        if not self._playing or self._loaded_uri is None or self._shutdown:
            return None
        if self._last_position_update_at <= 0.0:
            return None
        return time.monotonic() - self._last_position_update_at

    def _touch_position_clock(self) -> None:
        self._last_position_update_at = time.monotonic()

    def get_duration(self) -> float | None:
        """Return the last mpv duration observed on the IPC reader thread."""
        return self._duration_sec

    def is_playing(self) -> bool:
        if self._loaded_uri is None or self._shutdown:
            return False
        return self._playing

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
