"""User-facing messages for Linux audio device / path failures."""

from __future__ import annotations

DIRECT_ALSA_UNAVAILABLE = (
    "Could not open this ALSA device (likely in use via PipeWire). "
    "Enable Exclusive device access for bit-perfect alone, or choose the "
    "PipeWire output to share the device."
)

OUTPUT_UNAVAILABLE = (
    "Saved audio output is unavailable. Choose a device in Settings → Audio."
)

AUDIO_OUTPUT_STALLED = "Audio output stalled."

DIRECT_ALSA_STALLED = (
    "Audio output stalled on this ALSA device. "
    "Enable Exclusive device access, or choose the PipeWire output to share."
)
