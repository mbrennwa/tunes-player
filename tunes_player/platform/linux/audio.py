"""Linux audio sink volume via WirePlumber (wpctl) or PulseAudio (pactl)."""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import replace

from tunes_player.core.audio_labels import classify_sink_potential
from tunes_player.core.config import AppConfig
from tunes_player.core.volume import (
    SYSTEM_DEFAULT_SINK_ID,
    VolumeController,
    VolumeEndpoint,
    is_alsa_endpoint_id,
)

_SINK_LINE = re.compile(r"^\s*(?P<default>\*)?\s*(?P<id>\d+)\.\s+(?P<name>.+?)(?:\s+\[|$)")
_PACTL_VOLUME = re.compile(r"(\d+)%")
_WPCTL_VOLUME = re.compile(r"Volume:\s*([\d.]+)", re.IGNORECASE)
_PACTL_SINK_SHORT = re.compile(r"^(?P<id>\d+)\s+(?P<name>\S+)\s+(?P<desc>.+)$")
_WPCTL_TREE_CHARS = re.compile(r"[│├└─]")

def _system_default_endpoint(*, description: str | None = None) -> VolumeEndpoint:
    return VolumeEndpoint(
        id=SYSTEM_DEFAULT_SINK_ID,
        name="default",
        description=description or "System default",
        is_default=True,
        bit_perfect_potential="none",
    )


def _parse_wpctl_sink_line(line: str) -> re.Match[str] | None:
    """Parse a sink line from ``wpctl status`` (tree drawing chars break plain regex)."""
    if "." not in line:
        return None
    cleaned = _WPCTL_TREE_CHARS.sub(" ", line)
    return _SINK_LINE.search(cleaned)


def _parse_wpctl_status_sinks(stdout: str) -> list[VolumeEndpoint]:
    endpoints: list[VolumeEndpoint] = []
    in_sinks = False
    for line in stdout.splitlines():
        if line.rstrip().endswith("Sinks:"):
            in_sinks = True
            continue
        if not in_sinks:
            continue
        if line.rstrip().endswith("Sources:") or line.rstrip().endswith("Source outputs:"):
            break
        if _WPCTL_TREE_CHARS.search(line) and line.rstrip().endswith(":"):
            if endpoints:
                break
            continue
        match = _parse_wpctl_sink_line(line)
        if match is None:
            continue
        name = match.group("name").strip()
        endpoints.append(
            VolumeEndpoint(
                id=match.group("id"),
                name=name,
                description=name,
                is_default=match.group("default") == "*",
                bit_perfect_potential=classify_sink_potential(
                    name=name, description=name
                ),
            )
        )
    return endpoints


def _alsa_volume_endpoints() -> list[VolumeEndpoint]:
    from tunes_player.platform.linux.audio_probe import list_alsa_playback_endpoints

    endpoints: list[VolumeEndpoint] = []
    for endpoint_id, mpv_name, description in list_alsa_playback_endpoints():
        endpoints.append(
            VolumeEndpoint(
                id=endpoint_id,
                name=mpv_name,
                description=description,
                is_default=False,
                bit_perfect_potential="direct",
            )
        )
    return endpoints


def _mark_preferred_default(
    endpoints: list[VolumeEndpoint], *, configured_id: str | None
) -> list[VolumeEndpoint]:
    """Prefer saved id, else first ALSA (bit-perfect path), else PipeWire default."""
    if not endpoints:
        return endpoints
    chosen: str | None = None
    if configured_id and any(item.id == configured_id for item in endpoints):
        chosen = configured_id
    elif any(is_alsa_endpoint_id(item.id) for item in endpoints):
        chosen = next(item.id for item in endpoints if is_alsa_endpoint_id(item.id))
    else:
        for item in endpoints:
            if item.is_default:
                chosen = item.id
                break
    if chosen is None:
        chosen = endpoints[0].id
    return [replace(item, is_default=item.id == chosen) for item in endpoints]


def create_volume_controller(config: AppConfig) -> VolumeController:
    merged = LinuxOutputController(config)
    if merged.list_endpoints():
        return merged
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
        endpoints = self.list_endpoints()
        if configured:
            ids = {item.id for item in endpoints}
            if configured in ids:
                return configured
        for endpoint in endpoints:
            if endpoint.is_default:
                return endpoint.id
        return endpoints[0].id if endpoints else None

    def set_active_endpoint(self, endpoint_id: str) -> None:
        """Persist Tunes output choice only — does not change the system default sink."""
        self._config.output_sink_id = endpoint_id
        self._cached_endpoints = None

    def mpv_audio_device(self) -> str | None:
        endpoint_id = self.get_active_endpoint_id()
        if endpoint_id is None or endpoint_id == SYSTEM_DEFAULT_SINK_ID:
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

    def _wpctl_volume_target(self) -> str:
        endpoint_id = self.get_active_endpoint_id()
        if endpoint_id is None or endpoint_id == SYSTEM_DEFAULT_SINK_ID:
            return "@DEFAULT_AUDIO_SINK@"
        return endpoint_id

    def _get_volume_args(self) -> list[str]:
        return ["get-volume", self._wpctl_volume_target()]

    def _set_volume_args(self, level: float) -> list[str]:
        return ["set-volume", self._wpctl_volume_target(), f"{level:.4f}"]

    def _list_endpoints(self) -> list[VolumeEndpoint]:
        result = self._run(["wpctl", "status"], check=True)
        endpoints = _parse_wpctl_status_sinks(result.stdout)
        if endpoints:
            return endpoints
        try:
            named = self._run(["wpctl", "status", "-n"], check=True)
        except (OSError, subprocess.CalledProcessError):
            return []
        return _parse_wpctl_status_sinks(named.stdout)

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

    def _list_endpoints(self) -> list[VolumeEndpoint]:
        default_name = self._run(["pactl", "get-default-sink"], check=True).stdout.strip()
        result = self._run(["pactl", "list", "sinks", "short"], check=True)
        endpoints: list[VolumeEndpoint] = []
        for line in result.stdout.splitlines():
            match = _PACTL_SINK_SHORT.match(line.strip())
            if match is None:
                continue
            name = match.group("name")
            desc = match.group("desc").strip()
            endpoints.append(
                VolumeEndpoint(
                    id=match.group("id"),
                    name=name,
                    description=desc,
                    is_default=name == default_name,
                    bit_perfect_potential=classify_sink_potential(
                        name=name, description=desc
                    ),
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


class LinuxOutputController:
    """ALSA devices (preferred) plus PipeWire/Pulse sinks when available."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._cached_endpoints: list[VolumeEndpoint] | None = None
        self._sink_backend: _SubprocessVolumeController | None = None
        wpctl = WpctlVolumeController(config)
        if wpctl.available():
            self._sink_backend = wpctl
        else:
            pactl = PactlVolumeController(config)
            if pactl.available():
                self._sink_backend = pactl
        self._software_level = 0.72

    def _active_alsa_card(self) -> int | None:
        active = self.get_active_endpoint_id()
        if active is None or not is_alsa_endpoint_id(active):
            return None
        from tunes_player.platform.linux.alsa_mixer import alsa_card_from_endpoint_id

        return alsa_card_from_endpoint_id(active)

    def _alsa_has_hardware_volume(self) -> bool:
        card = self._active_alsa_card()
        if card is None:
            return False
        from tunes_player.platform.linux.alsa_mixer import alsa_mixer_available

        return alsa_mixer_available(card)

    @property
    def uses_device_volume(self) -> bool:
        active = self.get_active_endpoint_id()
        if active is None or active == SYSTEM_DEFAULT_SINK_ID:
            return False
        if is_alsa_endpoint_id(active):
            return self._alsa_has_hardware_volume()
        return self._sink_backend is not None

    def available(self) -> bool:
        return bool(self._alsa_volume_endpoints() or self._list_sink_endpoints())

    def get_level(self) -> float:
        card = self._active_alsa_card()
        if card is not None and self._alsa_has_hardware_volume():
            from tunes_player.platform.linux.alsa_mixer import alsa_get_level

            return alsa_get_level(card)
        if self.uses_device_volume and self._sink_backend is not None:
            return self._sink_backend.get_level()
        return self._software_level

    def set_level(self, level: float) -> None:
        clamped = max(0.0, min(1.0, level))
        card = self._active_alsa_card()
        if card is not None and self._alsa_has_hardware_volume():
            from tunes_player.platform.linux.alsa_mixer import alsa_set_level

            alsa_set_level(card, clamped)
            return
        if self.uses_device_volume and self._sink_backend is not None:
            self._sink_backend.set_level(clamped)
            return
        self._software_level = clamped

    def adjust_level(self, delta: float) -> None:
        if self.uses_device_volume:
            self.set_level(self.get_level() + delta)
            return
        self.set_level(self._software_level + delta)

    def list_endpoints(self) -> list[VolumeEndpoint]:
        if self._cached_endpoints is None:
            merged = self._alsa_volume_endpoints() + self._list_sink_endpoints()
            self._cached_endpoints = _mark_preferred_default(
                merged, configured_id=self._config.output_sink_id
            )
        return list(self._cached_endpoints)

    def get_active_endpoint_id(self) -> str | None:
        endpoints = self.list_endpoints()
        if not endpoints:
            return None
        configured = self._config.output_sink_id
        if configured:
            ids = {item.id for item in endpoints}
            if configured in ids:
                return configured
        for endpoint in endpoints:
            if endpoint.is_default:
                return endpoint.id
        return endpoints[0].id

    def set_active_endpoint(self, endpoint_id: str) -> None:
        self._config.output_sink_id = endpoint_id
        self._cached_endpoints = None

    def mpv_audio_device(self) -> str | None:
        endpoint_id = self.get_active_endpoint_id()
        if endpoint_id is None or endpoint_id == SYSTEM_DEFAULT_SINK_ID:
            return None
        for endpoint in self.list_endpoints():
            if endpoint.id != endpoint_id:
                continue
            if is_alsa_endpoint_id(endpoint_id):
                return f"alsa/{endpoint.name}"
            if self._sink_backend is not None:
                return self._sink_backend._mpv_device_for(endpoint)
        return None

    def _alsa_volume_endpoints(self) -> list[VolumeEndpoint]:
        return _alsa_volume_endpoints()

    def _list_sink_endpoints(self) -> list[VolumeEndpoint]:
        if self._sink_backend is None:
            return []
        return self._sink_backend._list_endpoints()


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
        return [_system_default_endpoint()]

    def get_active_endpoint_id(self) -> str | None:
        configured = self._config.output_sink_id
        endpoints = self.list_endpoints()
        if configured:
            ids = {item.id for item in endpoints}
            if configured in ids:
                return configured
        return endpoints[0].id if endpoints else None

    def set_active_endpoint(self, endpoint_id: str) -> None:
        self._config.output_sink_id = endpoint_id

    def mpv_audio_device(self) -> str | None:
        return None
