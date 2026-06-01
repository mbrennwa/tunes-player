"""Linux audio sink volume via WirePlumber (wpctl) or PulseAudio (pactl)."""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from abc import ABC, abstractmethod

from tunes_player.core.config import AppConfig
from tunes_player.core.volume import VolumeController, VolumeEndpoint

_SINK_LINE = re.compile(r"^\s*(?P<default>\*)?\s*(?P<id>\d+)\.\s+(?P<name>.+?)(?:\s+\[|$)")
_PACTL_VOLUME = re.compile(r"(\d+)%")
_WPCTL_VOLUME = re.compile(r"Volume:\s*([\d.]+)", re.IGNORECASE)
_PACTL_SINK_SHORT = re.compile(r"^(?P<id>\d+)\s+(?P<name>\S+)\s+(?P<desc>.+)$")


def create_volume_controller(config: AppConfig) -> VolumeController:
    for cls in (WpctlVolumeController, PactlVolumeController):
        controller = cls(config)
        if controller.available():
            return controller
    return NullVolumeController(config)


def mpv_playback_options(
    *,
    bit_perfect: bool,
    audio_device: str | None,
    software_volume: float,
) -> dict[str, object]:
    """Build mpv constructor options for the selected output profile."""
    options: dict[str, object] = {}
    if bit_perfect:
        options["volume"] = 100
        options["replaygain"] = "no"
    else:
        options["volume"] = max(0, min(100, int(round(software_volume * 100))))
    if audio_device:
        options["audio_device"] = audio_device
    return options


class _SubprocessVolumeController(ABC):
    uses_device_volume = True

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._cached_endpoints: list[VolumeEndpoint] | None = None
        self._volume_lock = threading.Lock()
        self._pending_level: float | None = None
        self._apply_thread: threading.Thread | None = None

    @property
    @abstractmethod
    def _command(self) -> str: ...

    def available(self) -> bool:
        if shutil.which(self._command) is None:
            return False
        try:
            self._run([self._command, *self._probe_args()], check=True)
        except (OSError, subprocess.CalledProcessError):
            return False
        return True

    @abstractmethod
    def _probe_args(self) -> list[str]: ...

    def get_level(self) -> float:
        raw = self._run([self._command, *self._get_volume_args()], check=True).stdout.strip()
        return self._parse_level(raw)

    def set_level(self, level: float) -> None:
        clamped = max(0.0, min(1.0, level))
        with self._volume_lock:
            self._pending_level = clamped
            if self._apply_thread is not None and self._apply_thread.is_alive():
                return
            self._apply_thread = threading.Thread(
                target=self._apply_pending_levels,
                name=f"{self._command}-volume",
                daemon=True,
            )
            self._apply_thread.start()

    def _apply_pending_levels(self) -> None:
        while True:
            with self._volume_lock:
                level = self._pending_level
                self._pending_level = None
            if level is None:
                return
            try:
                self._run([self._command, *self._set_volume_args(level)], check=True)
            except (OSError, subprocess.CalledProcessError):
                return
            time.sleep(0.025)
            with self._volume_lock:
                if self._pending_level is None:
                    return

    def adjust_level(self, delta: float) -> None:
        self.set_level(self.get_level() + delta)

    def list_endpoints(self) -> list[VolumeEndpoint]:
        if self._cached_endpoints is None:
            self._cached_endpoints = self._list_endpoints()
        return list(self._cached_endpoints)

    def get_active_endpoint_id(self) -> str | None:
        configured = self._config.output_sink_id
        if configured:
            ids = {item.id for item in self.list_endpoints()}
            if configured in ids:
                return configured
        for endpoint in self.list_endpoints():
            if endpoint.is_default:
                return endpoint.id
        endpoints = self.list_endpoints()
        return endpoints[0].id if endpoints else None

    def set_active_endpoint(self, endpoint_id: str) -> None:
        self._config.output_sink_id = endpoint_id
        self._run([self._command, *self._set_default_args(endpoint_id)], check=True)
        self._cached_endpoints = None

    def mpv_audio_device(self) -> str | None:
        endpoint_id = self.get_active_endpoint_id()
        if endpoint_id is None:
            return None
        for endpoint in self.list_endpoints():
            if endpoint.id == endpoint_id:
                return self._mpv_device_for(endpoint)
        return None

    @abstractmethod
    def _get_volume_args(self) -> list[str]: ...

    @abstractmethod
    def _set_volume_args(self, level: float) -> list[str]: ...

    @abstractmethod
    def _set_default_args(self, endpoint_id: str) -> list[str]: ...

    @abstractmethod
    def _list_endpoints(self) -> list[VolumeEndpoint]: ...

    @abstractmethod
    def _parse_level(self, raw: str) -> float: ...

    @abstractmethod
    def _mpv_device_for(self, endpoint: VolumeEndpoint) -> str | None: ...

    def _run(self, args: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            check=check,
            capture_output=True,
            text=True,
        )


class WpctlVolumeController(_SubprocessVolumeController):
    _command = "wpctl"

    def _probe_args(self) -> list[str]:
        return ["status"]

    def _get_volume_args(self) -> list[str]:
        endpoint_id = self.get_active_endpoint_id()
        target = endpoint_id or "@DEFAULT_AUDIO_SINK@"
        return ["get-volume", target]

    def _set_volume_args(self, level: float) -> list[str]:
        endpoint_id = self.get_active_endpoint_id()
        target = endpoint_id or "@DEFAULT_AUDIO_SINK@"
        return ["set-volume", target, f"{level:.4f}"]

    def _set_default_args(self, endpoint_id: str) -> list[str]:
        return ["set-default", endpoint_id]

    def _list_endpoints(self) -> list[VolumeEndpoint]:
        result = self._run(["wpctl", "status"], check=True)
        endpoints: list[VolumeEndpoint] = []
        in_sinks = False
        for line in result.stdout.splitlines():
            if line.startswith("Sinks:"):
                in_sinks = True
                continue
            if in_sinks:
                if not line.startswith(" │"):
                    if endpoints:
                        break
                    continue
                match = _SINK_LINE.search(line)
                if match is None:
                    continue
                name = match.group("name").strip()
                endpoints.append(
                    VolumeEndpoint(
                        id=match.group("id"),
                        name=name,
                        description=name,
                        is_default=match.group("default") == "*",
                    )
                )
        return endpoints

    def _parse_level(self, raw: str) -> float:
        match = _WPCTL_VOLUME.search(raw)
        if match is not None:
            value = float(match.group(1))
        else:
            # Older wpctl printed a bare fraction, e.g. "0.40"
            value = float(raw.split()[0])
        return max(0.0, min(1.0, value))

    def _mpv_device_for(self, endpoint: VolumeEndpoint) -> str | None:
        # mpv pulse output works with PipeWire via pipewire-pulse (pactl CLI not required).
        return f"pulse/{endpoint.name}"


class PactlVolumeController(_SubprocessVolumeController):
    _command = "pactl"

    def _probe_args(self) -> list[str]:
        return ["info"]

    def _get_volume_args(self) -> list[str]:
        sink = self._active_sink_name()
        return ["get-sink-volume", sink]

    def _set_volume_args(self, level: float) -> list[str]:
        sink = self._active_sink_name()
        percent = int(round(level * 100))
        return ["set-sink-volume", sink, f"{percent}%"]

    def _set_default_args(self, endpoint_id: str) -> list[str]:
        sink_name = self._sink_name_for_id(endpoint_id)
        return ["set-default-sink", sink_name]

    def _list_endpoints(self) -> list[VolumeEndpoint]:
        default_name = self._run(["pactl", "get-default-sink"], check=True).stdout.strip()
        result = self._run(["pactl", "list", "sinks", "short"], check=True)
        endpoints: list[VolumeEndpoint] = []
        for line in result.stdout.splitlines():
            match = _PACTL_SINK_SHORT.match(line.strip())
            if match is None:
                continue
            name = match.group("name")
            endpoints.append(
                VolumeEndpoint(
                    id=match.group("id"),
                    name=name,
                    description=match.group("desc").strip(),
                    is_default=name == default_name,
                )
            )
        return endpoints

    def _parse_level(self, raw: str) -> float:
        match = _PACTL_VOLUME.search(raw)
        if match is None:
            return 0.72
        return max(0.0, min(1.0, int(match.group(1)) / 100))

    def _mpv_device_for(self, endpoint: VolumeEndpoint) -> str:
        return f"pulse/{endpoint.name}"

    def _active_sink_name(self) -> str:
        endpoint_id = self.get_active_endpoint_id()
        if endpoint_id is not None:
            return self._sink_name_for_id(endpoint_id)
        return self._run(["pactl", "get-default-sink"], check=True).stdout.strip()

    def _sink_name_for_id(self, endpoint_id: str) -> str:
        for endpoint in self.list_endpoints():
            if endpoint.id == endpoint_id:
                return endpoint.name
        return self._run(["pactl", "get-default-sink"], check=True).stdout.strip()


class NullVolumeController:
    """Software volume fallback when no sink controller is available."""

    uses_device_volume = False

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._level = 0.72

    def available(self) -> bool:
        return True

    def get_level(self) -> float:
        return self._level

    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, level))

    def adjust_level(self, delta: float) -> None:
        self.set_level(self._level + delta)

    def list_endpoints(self) -> list[VolumeEndpoint]:
        return []

    def get_active_endpoint_id(self) -> str | None:
        return None

    def set_active_endpoint(self, endpoint_id: str) -> None:
        self._config.output_sink_id = endpoint_id

    def mpv_audio_device(self) -> str | None:
        return None
