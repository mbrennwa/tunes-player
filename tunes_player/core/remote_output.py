"""Remote MPD output definitions (#60).

A remote output is a user-named MPD instance elsewhere on the network. Tunes
only drives MPD; how MPD reaches its DAC or DSP is configured outside Tunes.
"""

from __future__ import annotations

from dataclasses import dataclass

from tunes_player.core.volume import (
    VolumeEndpoint,
    is_mpd_endpoint_id,
    mpd_endpoint_id,
)

DEFAULT_MPD_PORT = 6600


class RemoteTargetError(RuntimeError):
    """A track cannot be handed to a remote MPD (message is user-facing)."""


@dataclass(frozen=True, slots=True)
class RemoteMpdOutput:
    """One configured remote MPD endpoint."""

    name: str
    host: str
    port: int = DEFAULT_MPD_PORT

    @property
    def id(self) -> str:
        return mpd_endpoint_id(self.host, self.port)

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    def to_endpoint(self) -> VolumeEndpoint:
        """Output-list entry; remote outputs never offer Tunes volume control."""
        return VolumeEndpoint(
            id=self.id,
            name=self.name,
            description=f"{self.address} · network MPD",
            bit_perfect_potential="none",
        )


def normalize_remote_output(
    name: object, host: object, port: object = DEFAULT_MPD_PORT
) -> RemoteMpdOutput | None:
    """Validate user input; None when the host or port is unusable."""
    host_text = str(host or "").strip()
    if not host_text or any(ch.isspace() for ch in host_text) or ":" in host_text.strip("[]"):
        return None
    try:
        port_number = int(port)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None
    if not 1 <= port_number <= 65535:
        return None
    name_text = str(name or "").strip() or host_text
    return RemoteMpdOutput(name=name_text, host=host_text, port=port_number)


def parse_remote_outputs(raw: object) -> list[RemoteMpdOutput]:
    """Load the persisted list, dropping malformed and duplicate entries."""
    if not isinstance(raw, list):
        return []
    outputs: list[RemoteMpdOutput] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        output = normalize_remote_output(
            item.get("name"), item.get("host"), item.get("port", DEFAULT_MPD_PORT)
        )
        if output is None or output.id in seen:
            continue
        seen.add(output.id)
        outputs.append(output)
    return outputs


def remote_outputs_to_json(outputs: list[RemoteMpdOutput]) -> list[dict[str, object]]:
    return [
        {"name": item.name, "host": item.host, "port": item.port} for item in outputs
    ]


def is_remote_output_id(endpoint_id: str | None) -> bool:
    return is_mpd_endpoint_id(endpoint_id)
