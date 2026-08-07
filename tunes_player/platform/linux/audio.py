"""Linux audio sink volume via WirePlumber (wpctl) or PulseAudio (pactl)."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import replace

from tunes_player.core.audio_labels import classify_sink_potential
from tunes_player.core.config import AppConfig
from tunes_player.core.volume import (
    SYSTEM_DEFAULT_SINK_ID,
    Unsubscribe,
    VolumeController,
    VolumeEndpoint,
    VolumeListener,
    VolumeSubscriptionHub,
    is_alsa_endpoint_id,
    pipewire_endpoint_id,
)

_SINK_LINE = re.compile(r"^\s*(?P<default>\*)?\s*(?P<id>\d+)\.\s+(?P<name>.+?)(?:\s+\[|$)")
_PACTL_VOLUME = re.compile(r"(\d+)%")
_WPCTL_VOLUME = re.compile(r"Volume:\s*([\d.]+)", re.IGNORECASE)

log = logging.getLogger(__name__)
_PACTL_SINK_SHORT = re.compile(r"^(?P<id>\d+)\s+(?P<name>\S+)\s+(?P<desc>.+)$")
_WPCTL_TREE_CHARS = re.compile(r"[│├└─]")
# wpctl inspect marks some properties with a leading "*".
_NODE_NAME = re.compile(r'^\s*\*?\s*node\.name\s*=\s*"([^"]+)"')
_NODE_DESCRIPTION = re.compile(r'^\s*\*?\s*node\.description\s*=\s*"([^"]+)"')
_ALSA_CARD = re.compile(r'^\s*\*?\s*alsa\.card\s*=\s*"(\d+)"')
_ALSA_DEVICE = re.compile(r'^\s*\*?\s*alsa\.device\s*=\s*"(\d+)"')

def _system_default_endpoint(*, description: str | None = None) -> VolumeEndpoint:
    return VolumeEndpoint(
        id=SYSTEM_DEFAULT_SINK_ID,
        name="default",
        description=description or "System default",
        is_default=True,
        bit_perfect_potential="none",
    )

def _wpctl_inspect_sink(sink_id: str) -> tuple[str | None, str | None]:
    """Return (node.name, node.description) for a wpctl sink id."""
    if shutil.which("wpctl") is None:
        return None, None
    try:
        result = subprocess.run(
            ["wpctl", "inspect", sink_id],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None, None
    node_name: str | None = None
    node_description: str | None = None
    for line in result.stdout.splitlines():
        name_match = _NODE_NAME.match(line)
        if name_match:
            node_name = name_match.group(1)
            continue
        desc_match = _NODE_DESCRIPTION.match(line)
        if desc_match:
            node_description = desc_match.group(1)
    return node_name, node_description

def _wpctl_inspect_alsa_pcm(sink_id: str) -> tuple[int, int] | None:
    """Return (card, device) from wpctl inspect when the sink is ALSA-backed."""
    if shutil.which("wpctl") is None:
        return None
    try:
        result = subprocess.run(
            ["wpctl", "inspect", sink_id],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    card: int | None = None
    device: int | None = None
    for line in result.stdout.splitlines():
        card_match = _ALSA_CARD.match(line)
        if card_match is not None:
            card = int(card_match.group(1))
            continue
        device_match = _ALSA_DEVICE.match(line)
        if device_match is not None:
            device = int(device_match.group(1))
    if card is None or device is None:
        return None
    return card, device

def resolve_alsa_hw_endpoint_id(endpoint: VolumeEndpoint) -> str | None:
    """Map a listed endpoint to ``alsa:hw:C:D`` when the stack exposes that PCM."""
    if is_alsa_endpoint_id(endpoint.id):
        return endpoint.id
    if endpoint.control_id is None:
        return None
    pcm = _wpctl_inspect_alsa_pcm(endpoint.control_id)
    if pcm is None:
        return None
    card, device = pcm
    return f"alsa:hw:{card}:{device}"

def _parse_wpctl_sink_line(line: str) -> re.Match[str] | None:
    """Parse a sink line from ``wpctl status`` (tree drawing chars break plain regex)."""
    if "." not in line:
        return None
    cleaned = _WPCTL_TREE_CHARS.sub(" ", line)
    return _SINK_LINE.search(cleaned)

def _parse_wpctl_status_sinks(stdout: str) -> list[VolumeEndpoint]:
    """Parse Audio Sinks plus Filters ``[Audio/Sink]`` (e.g. software-DSP Speakers)."""
    endpoints: list[VolumeEndpoint] = []
    # "sinks" | "filters" | None (skip Sources / other Audio subsections)
    section: str | None = None
    for line in stdout.splitlines():
        stripped = line.rstrip()
        if stripped.endswith("Sinks:"):
            section = "sinks"
            continue
        if stripped.endswith("Filters:"):
            section = "filters"
            continue
        if stripped.endswith("Sources:") or stripped.endswith("Source outputs:"):
            if section == "sinks":
                section = None
            continue
        if stripped.endswith("Streams:") or stripped.endswith("Devices:"):
            if section == "filters" or stripped.endswith("Streams:"):
                section = None
            continue
        if section is None:
            continue
        if _WPCTL_TREE_CHARS.search(line) and stripped.endswith(":"):
            continue
        if section == "filters" and "[Audio/Sink]" not in line:
            continue
        match = _parse_wpctl_sink_line(line)
        if match is None:
            continue
        status_label = match.group("name").strip()
        sink_id = match.group("id")
        node_name, node_description = _wpctl_inspect_sink(sink_id)
        stable_name = node_name or status_label
        description = node_description or status_label
        endpoints.append(
            VolumeEndpoint(
                id=pipewire_endpoint_id(stable_name),
                name=stable_name,
                description=description,
                is_default=match.group("default") == "*",
                bit_perfect_potential=classify_sink_potential(
                    name=stable_name, description=description
                ),
                control_id=sink_id,
            )
        )
    return endpoints

def _alsa_volume_endpoints() -> list[VolumeEndpoint]:
    from tunes_player.platform.linux.alsa_mixer import (
        alsa_card_from_endpoint_id,
        alsa_device_from_endpoint_id,
    )
    from tunes_player.platform.linux.audio_probe import list_alsa_playback_endpoints
    from tunes_player.platform.linux.pipewire_claimed_alsa import (
        pipewire_claimed_alsa_pcms,
    )

    claimed = pipewire_claimed_alsa_pcms()
    endpoints: list[VolumeEndpoint] = []
    for endpoint_id, mpv_name, description in list_alsa_playback_endpoints():
        card = alsa_card_from_endpoint_id(endpoint_id)
        device = alsa_device_from_endpoint_id(endpoint_id)
        if (
            card is not None
            and device is not None
            and (card, device) in claimed
        ):
            continue
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
    """Prefer saved id, else PipeWire/Pulse default, else first unclaimed ALSA."""
    if not endpoints:
        return endpoints
    chosen: str | None = None
    if configured_id and any(item.id == configured_id for item in endpoints):
        chosen = configured_id
    else:
        for item in endpoints:
            if item.is_default:
                chosen = item.id
                break
        if chosen is None:
            for item in endpoints:
                if is_alsa_endpoint_id(item.id):
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

class _SubprocessVolumeController(ABC):
    uses_device_volume = True

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._cached_endpoints: list[VolumeEndpoint] | None = None
        self._subscriptions = VolumeSubscriptionHub()

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
        try:
            self._run([self._command, *self._set_volume_args(clamped)], check=True)
        except (OSError, subprocess.CalledProcessError):
            log.debug("Could not set %s output volume", self._command, exc_info=True)
            return
        self._subscriptions.notify(clamped)

    def adjust_level(self, delta: float) -> None:
        self.set_level(self.get_level() + delta)

    def subscribe(self, listener: VolumeListener) -> Unsubscribe:
        return self._subscriptions.subscribe(listener)

    def notify_external_level(self, level: float) -> None:
        """Report an inbound stack volume change (foundation for #104)."""
        self._subscriptions.notify(level)

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
        # Use cached list_endpoints() — never re-run wpctl status per set/get.
        for endpoint in self.list_endpoints():
            if endpoint.id == endpoint_id and endpoint.control_id is not None:
                return endpoint.control_id
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
                    id=pipewire_endpoint_id(name),
                    name=name,
                    description=desc,
                    is_default=name == default_name,
                    bit_perfect_potential=classify_sink_potential(
                        name=name, description=desc
                    ),
                    control_id=match.group("id"),
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
        self._subscriptions = VolumeSubscriptionHub()
        self._sink_backend: _SubprocessVolumeController | None = None
        wpctl = WpctlVolumeController(config)
        if wpctl.available():
            self._sink_backend = wpctl
        else:
            pactl = PactlVolumeController(config)
            if pactl.available():
                self._sink_backend = pactl
        self._software_level = 0.72

    def _active_alsa_endpoint_id(self) -> str | None:
        active = self.get_active_endpoint_id()
        if active is None or not is_alsa_endpoint_id(active):
            return None
        return active

    def _alsa_has_hardware_volume(self) -> bool:
        endpoint_id = self._active_alsa_endpoint_id()
        if endpoint_id is None:
            return False
        from tunes_player.platform.linux.alsa_mixer import alsa_mixer_adjustable_for_endpoint

        return alsa_mixer_adjustable_for_endpoint(endpoint_id)

    def _active_endpoint(self) -> VolumeEndpoint | None:
        active = self.get_active_endpoint_id()
        if active is None:
            return None
        for endpoint in self.list_endpoints():
            if endpoint.id == active:
                return endpoint
        return None

    def _resolved_alsa_hw_endpoint_id(self) -> str | None:
        endpoint = self._active_endpoint()
        if endpoint is None:
            return None
        return resolve_alsa_hw_endpoint_id(endpoint)

    @property
    def uses_device_volume(self) -> bool:
        active = self.get_active_endpoint_id()
        if active is None or active == SYSTEM_DEFAULT_SINK_ID:
            return False
        resolved = self._resolved_alsa_hw_endpoint_id()
        if resolved is not None:
            from tunes_player.platform.linux.alsa_mixer import (
                alsa_mixer_adjustable_for_endpoint,
            )

            return alsa_mixer_adjustable_for_endpoint(resolved)
        if is_alsa_endpoint_id(active):
            return self._alsa_has_hardware_volume()
        return self._sink_backend is not None

    def available(self) -> bool:
        return bool(self._alsa_volume_endpoints() or self._list_sink_endpoints())

    def get_level(self) -> float:
        endpoint_id = self._active_alsa_endpoint_id()
        if endpoint_id is not None and self._alsa_has_hardware_volume():
            from tunes_player.platform.linux.alsa_mixer import alsa_get_level_for_endpoint

            return alsa_get_level_for_endpoint(endpoint_id)
        if self.uses_device_volume and self._sink_backend is not None:
            return self._sink_backend.get_level()
        return self._software_level

    def set_level(self, level: float) -> None:
        clamped = max(0.0, min(1.0, level))
        endpoint_id = self._active_alsa_endpoint_id()
        if endpoint_id is not None and self._alsa_has_hardware_volume():
            from tunes_player.platform.linux.alsa_mixer import alsa_set_level_for_endpoint

            alsa_set_level_for_endpoint(endpoint_id, clamped)
            self._subscriptions.notify(clamped)
            return
        if self.uses_device_volume and self._sink_backend is not None:
            self._sink_backend.set_level(clamped)
            self._subscriptions.notify(clamped)
            return
        self._software_level = clamped
        self._subscriptions.notify(clamped)

    def adjust_level(self, delta: float) -> None:
        if self.uses_device_volume:
            self.set_level(self.get_level() + delta)
            return
        self.set_level(self._software_level + delta)

    def subscribe(self, listener: VolumeListener) -> Unsubscribe:
        return self._subscriptions.subscribe(listener)

    def notify_external_level(self, level: float) -> None:
        """Report an inbound stack volume change (foundation for #104)."""
        self._subscriptions.notify(level)

    def list_endpoints(self) -> list[VolumeEndpoint]:
        if self._cached_endpoints is None:
            merged = self._alsa_volume_endpoints() + self._list_sink_endpoints()
            configured = self._normalize_output_sink_id(merged)
            self._cached_endpoints = _mark_preferred_default(
                merged, configured_id=configured
            )
        return list(self._cached_endpoints)

    def normalize_output_sink_config(self) -> bool:
        """Migrate legacy wpctl numeric ids to stable ids; return True if config changed."""
        self._cached_endpoints = None
        merged = self._alsa_volume_endpoints() + self._list_sink_endpoints()
        before = self._config.output_sink_id
        after = self._normalize_output_sink_id(merged)
        self._cached_endpoints = None
        return before != after

    def _normalize_output_sink_id(self, endpoints: list[VolumeEndpoint]) -> str | None:
        configured = self._config.output_sink_id
        if not configured or not endpoints:
            return configured
        ids = {item.id for item in endpoints}
        if configured in ids:
            return configured
        if configured.isdigit():
            for item in endpoints:
                if item.control_id == configured:
                    self._config.output_sink_id = item.id
                    return item.id
        from tunes_player.core.volume import pipewire_name_from_endpoint_id

        saved_name = pipewire_name_from_endpoint_id(configured)
        if saved_name:
            for item in endpoints:
                if item.name == saved_name or item.description == saved_name:
                    self._config.output_sink_id = item.id
                    return item.id
        for item in endpoints:
            if item.name == configured or item.description == configured:
                self._config.output_sink_id = item.id
                return item.id
        # Stale id (missing from live list after migration attempts).
        self._config.output_sink_id = None
        return None

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
        from tunes_player.platform.linux.alsa_mixer import clear_alsa_mixer_cache

        clear_alsa_mixer_cache()
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

    def exclusive_access_supported(self) -> bool:
        return is_alsa_endpoint_id(self.get_active_endpoint_id())

    def active_alsa_card(self) -> int | None:
        endpoint_id = self._active_alsa_endpoint_id()
        if endpoint_id is None:
            return None
        from tunes_player.platform.linux.alsa_mixer import alsa_card_from_endpoint_id

        return alsa_card_from_endpoint_id(endpoint_id)

class NullVolumeController:
    """Software volume fallback when no sink controller is available."""

    uses_device_volume = False

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._level = 0.72
        self._subscriptions = VolumeSubscriptionHub()

    def available(self) -> bool:
        return True

    def get_level(self) -> float:
        return self._level

    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, level))
        self._subscriptions.notify(self._level)

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

    def subscribe(self, listener: VolumeListener) -> Unsubscribe:
        return self._subscriptions.subscribe(listener)

    def notify_external_level(self, level: float) -> None:
        """Report an inbound stack volume change (foundation for #104)."""
        self._subscriptions.notify(level)
