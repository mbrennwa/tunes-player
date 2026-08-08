"""Volume control abstractions — endpoint volume, not player soft gain."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Literal, Protocol

VolumeListener = Callable[[float], None]
Unsubscribe = Callable[[], None]

BitPerfectPotential = Literal["direct", "capable", "none"]
VolumeMode = Literal["hardware", "software", "fixed"]


def derive_volume_mode(
    *,
    device_volume: bool,
    mpv_soft_volume: bool,
) -> VolumeMode:
    if device_volume:
        return "hardware"
    if mpv_soft_volume:
        return "software"
    return "fixed"


def debug_isolate_volume_from_stack() -> bool:
    """Debug: ignore inbound OS/GNOME → Tunes volume updates.

    When ``TUNES_DEBUG_VOLUME_NO_STACK_SYNC`` is set (``1``/``true``/``yes``/``on``):
    stack watcher / subscribe changes do not move Tunes' volume. Outbound
    Tunes → device/sink applies stay enabled (hardware volume still works).
    """
    raw = os.environ.get("TUNES_DEBUG_VOLUME_NO_STACK_SYNC", "").strip().casefold()
    return raw in {"1", "true", "yes", "on"}


def debug_volume_trace_enabled() -> bool:
    """When ``TUNES_DEBUG_VOLUME=1`` (or true/yes/on), log slider vs device applies."""
    raw = os.environ.get("TUNES_DEBUG_VOLUME", "").strip().casefold()
    return raw in {"1", "true", "yes", "on"}


def debug_volume_trace(msg: str, *args: object) -> None:
    """Emit a volume-debug line when :func:`debug_volume_trace_enabled`."""
    if not debug_volume_trace_enabled():
        return
    import logging

    logging.getLogger("tunes_player.volume").info(msg, *args)

SYSTEM_DEFAULT_SINK_ID = "__system_default__"


def is_alsa_endpoint_id(endpoint_id: str | None) -> bool:
    return bool(endpoint_id and endpoint_id.startswith("alsa:"))


def pipewire_endpoint_id(sink_name: str) -> str:
    """Stable config id for a PipeWire/Pulse sink (wpctl numeric ids change)."""
    return f"pw:{sink_name}"


def is_pipewire_endpoint_id(endpoint_id: str | None) -> bool:
    return bool(endpoint_id and endpoint_id.startswith("pw:"))


def pipewire_name_from_endpoint_id(endpoint_id: str) -> str | None:
    if not is_pipewire_endpoint_id(endpoint_id):
        return None
    return endpoint_id[3:]


@dataclass(frozen=True, slots=True)
class VolumeEndpoint:
    id: str
    name: str
    description: str
    is_default: bool = False
    bit_perfect_potential: BitPerfectPotential = "none"
    # wpctl/pactl target (numeric id); config uses stable ``id`` instead.
    control_id: str | None = None


@dataclass
class VolumeSubscriptionHub:
    """Fan-out for device/stack volume level changes (outbound apply + inbound watch)."""

    _listeners: list[VolumeListener] = field(default_factory=list, repr=False)

    def subscribe(self, listener: VolumeListener) -> Unsubscribe:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return unsubscribe

    def notify(self, level: float) -> None:
        clamped = max(0.0, min(1.0, level))
        for listener in list(self._listeners):
            listener(clamped)


class VolumeController(Protocol):
    """Adjust listening level on the OS audio sink, not inside the decoder."""

    @property
    def uses_device_volume(self) -> bool: ...

    def available(self) -> bool: ...

    def get_level(self) -> float: ...

    def set_level(self, level: float) -> None: ...

    def adjust_level(self, delta: float) -> None: ...

    def list_endpoints(self) -> list[VolumeEndpoint]: ...

    def get_active_endpoint_id(self) -> str | None: ...

    def set_active_endpoint(self, endpoint_id: str) -> None: ...

    def mpv_audio_device(self) -> str | None: ...

    def subscribe(self, listener: VolumeListener) -> Unsubscribe: ...
