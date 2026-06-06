"""Portable direct-ALSA playback settings (no RT/scheduling machine config)."""

from __future__ import annotations

import logging

from tunes_player.core.volume import is_alsa_endpoint_id
from tunes_player.platform.linux.alsa_mixer import alsa_card_from_endpoint_id, alsa_card_is_usb
from tunes_player.platform.linux.alsa_xrun_monitor import parse_card_from_mpv_device

LOG = logging.getLogger(__name__)

_USB_STABLE_NOTE = "USB direct"

_device_cache: dict[str, str | None] = {}
_logged_usb_stable: set[str] = set()


def clear_playback_device_cache() -> None:
    """Test helper — reset cached device mapping and log deduplication."""
    _device_cache.clear()
    _logged_usb_stable.clear()


def plughw_mpv_device(device: str | None) -> str | None:
    """Return a plughw mpv device string for the given hw/plughw device."""
    if not device:
        return None
    if "/hw:" in device:
        return device.replace("/hw:", "/plughw:", 1)
    if device.startswith("alsa/hw:"):
        return device.replace("alsa/hw:", "alsa/plughw:", 1)
    if device.startswith("hw:"):
        return f"alsa/plughw:{device[3:]}"
    return None


def alsa_card_for_playback(endpoint_id: str | None, mpv_device: str | None) -> int | None:
    if is_alsa_endpoint_id(endpoint_id):
        card = alsa_card_from_endpoint_id(endpoint_id or "")
        if card is not None:
            return card
    return parse_card_from_mpv_device(mpv_device)


def is_usb_alsa_playback(endpoint_id: str | None, mpv_device: str | None) -> bool:
    card = alsa_card_for_playback(endpoint_id, mpv_device)
    return card is not None and alsa_card_is_usb(card)


def effective_mpv_alsa_device(raw_device: str | None) -> str | None:
    """Keep hw: for USB; stability tuning is in subprocess mpv buffers and IRQ co-location."""
    if not raw_device:
        return None
    if raw_device in _device_cache:
        return _device_cache[raw_device]

    card = parse_card_from_mpv_device(raw_device)
    if card is not None and alsa_card_is_usb(card) and raw_device not in _logged_usb_stable:
        LOG.info(
            "USB ALSA card %d: direct hw output (%s)",
            card,
            raw_device,
        )
        _logged_usb_stable.add(raw_device)
    _device_cache[raw_device] = raw_device
    return raw_device


def usb_alsa_keep_device_open(endpoint_id: str | None, mpv_device: str | None) -> bool:
    """USB DACs stay open across track changes — ao-reload causes dropouts under load."""
    return is_usb_alsa_playback(endpoint_id, mpv_device)


def direct_alsa_use_exclusive(
    exclusive_enabled: bool,
    endpoint_id: str | None,
    mpv_device: str | None,
) -> bool:
    """Return whether mpv should open ALSA in exclusive mode."""
    del endpoint_id, mpv_device  # reserved for endpoint-specific policy later
    return exclusive_enabled


def portable_usb_playback_note(
    endpoint_id: str | None,
    mpv_device: str | None,
    *,
    exclusive_active: bool = False,
) -> str | None:
    if not is_usb_alsa_playback(endpoint_id, mpv_device) or exclusive_active:
        return None
    return _USB_STABLE_NOTE
