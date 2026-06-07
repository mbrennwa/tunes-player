"""Display labels for audio output endpoints (bit-perfect hint on direct ALSA only)."""

from __future__ import annotations

from tunes_player.core.volume import (
    BitPerfectPotential,
    VolumeEndpoint,
    is_alsa_endpoint_id,
)

_BIT_PERFECT_SUFFIX = " · bit perfect"


def classify_sink_potential(*, name: str, description: str) -> BitPerfectPotential:
    """Heuristic bit-perfect potential for PipeWire/Pulse sinks (no DAC database)."""
    text = f"{name} {description}".lower()
    virtual_markers = (
        "monitor",
        "null",
        "dummy",
        "easy effects",
        "zoom",
        "virtual",
        "echo-cancel",
        "remap",
    )
    if any(marker in text for marker in virtual_markers):
        return "none"
    if name.startswith("alsa_output.") and ".usb-" in name:
        return "capable"
    if "bluetooth" in text or "bluez" in text:
        return "capable"
    if any(marker in text for marker in ("hdmi", "displayport", " hda ")):
        return "capable"
    if name.startswith("alsa_output."):
        return "capable"
    return "none"


def endpoint_dropdown_label(endpoint: VolumeEndpoint) -> str:
    """Short label for Settings dropdowns (keeps Adw row layout readable)."""
    if is_alsa_endpoint_id(endpoint.id):
        return f"{endpoint.description}{_BIT_PERFECT_SUFFIX}"
    return endpoint.description


def endpoint_display_label(endpoint: VolumeEndpoint) -> str:
    return endpoint_dropdown_label(endpoint)
