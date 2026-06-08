"""Display labels for audio output endpoints (bit-perfect hint on direct ALSA only)."""

from __future__ import annotations

from tunes_player.core.volume import (
    BitPerfectPotential,
    VolumeEndpoint,
    is_alsa_endpoint_id,
)

_BIT_PERFECT_SUFFIX = " · bit perfect"
_DIGITAL_OUTPUT_MARKERS = ("hdmi", "displayport", "iec958", "spdif")


def endpoint_is_digital_output(endpoint: VolumeEndpoint) -> bool:
    """True when the endpoint is a fixed-level digital output (no HW attenuation)."""
    if is_alsa_endpoint_id(endpoint.id):
        from tunes_player.platform.linux.alsa_mixer import (
            alsa_card_from_endpoint_id,
            alsa_device_from_endpoint_id,
            alsa_pcm_device_is_digital_output,
        )

        card = alsa_card_from_endpoint_id(endpoint.id)
        device = alsa_device_from_endpoint_id(endpoint.id)
        if card is None or device is None:
            return False
        return alsa_pcm_device_is_digital_output(card, device)
    text = f"{endpoint.name} {endpoint.description}".casefold()
    return any(marker in text for marker in _DIGITAL_OUTPUT_MARKERS)


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
