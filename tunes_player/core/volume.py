"""Volume control abstractions — endpoint volume, not player soft gain."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Protocol

VolumeListener = Callable[[float], None]
Unsubscribe = Callable[[], None]

BitPerfectPotential = Literal["direct", "capable", "none"]

SYSTEM_DEFAULT_SINK_ID = "__system_default__"


def is_alsa_endpoint_id(endpoint_id: str | None) -> bool:
    return bool(endpoint_id and endpoint_id.startswith("alsa:"))


@dataclass(frozen=True, slots=True)
class VolumeEndpoint:
    id: str
    name: str
    description: str
    is_default: bool = False
    bit_perfect_potential: BitPerfectPotential = "none"


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
