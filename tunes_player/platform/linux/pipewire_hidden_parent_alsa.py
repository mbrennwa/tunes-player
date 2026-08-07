"""ALSA PCMs on cards replaced by WirePlumber software-DSP (hide-parent).

Asahi RawSpeakers uses ``hide-parent`` on ``hw:AppleJ*,1``. Opening other PCMs on
that same macaudio card (``Primary``) as bit-perfect ALSA is also silent/wrong for
Speakers — the usable path is the PipeWire DSP sink. So Tunes omits **all** playback
PCMs on any card that has a hide-parent rule. USB DACs and other cards without
software-DSP dual-list as ``alsa:hw:…`` · bit perfect.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

log = logging.getLogger(__name__)

_ALSA_PATH = re.compile(r'api\.alsa\.path\s*=\s*"([^"]+)"')
_HIDE_PARENT = re.compile(r"hide-parent\s*=\s*true\b")
_HW_PATH = re.compile(r"^(?:plug)?hw:([^,]+)(?:,(\d+))?$", re.IGNORECASE)
_CARD_LINE = re.compile(r"^\s*(\d+)\s*\[([^\]]+)\]")


def pipewire_hidden_parent_alsa_pcms() -> set[tuple[int, int]]:
    """Return playback ``(card, device)`` pairs to omit from the ALSA bit-perfect list.

    Resolves WirePlumber ``hide-parent`` paths, then expands to every playback PCM
    on those cards (Asahi Primary + Secondary share the Speakers DSP card).
    """
    paths = hidden_parent_alsa_paths_from_wireplumber_configs()
    if not paths:
        return set()
    cards = _alsa_card_name_to_index()
    whole_cards: set[int] = set()
    omitted: set[tuple[int, int]] = set()
    for path in paths:
        pcm = resolve_alsa_hw_path(path, cards=cards)
        if pcm is None:
            continue
        omitted.add(pcm)
        whole_cards.add(pcm[0])
    for card in whole_cards:
        omitted.update(_playback_devices_on_card(card))
    return omitted


def hidden_parent_alsa_paths_from_wireplumber_configs(
    *,
    config_dirs: list[Path] | None = None,
) -> set[str]:
    """Parse WirePlumber conf snippets for ``create-filter`` + ``hide-parent`` paths."""
    paths: set[str] = set()
    for directory in config_dirs if config_dirs is not None else _wireplumber_conf_dirs():
        if not directory.is_dir():
            continue
        for conf in sorted(directory.glob("*.conf")):
            try:
                text = conf.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                log.debug("Could not read WirePlumber conf %s: %s", conf, exc)
                continue
            paths.update(parse_hidden_parent_alsa_paths(text))
    return paths


def parse_hidden_parent_alsa_paths(text: str) -> set[str]:
    """Extract ``api.alsa.path`` values tied to ``hide-parent = true`` rules."""
    found: set[str] = set()
    for hide in _HIDE_PARENT.finditer(text):
        window = text[max(0, hide.start() - 1200) : hide.start()]
        matches = list(_ALSA_PATH.finditer(window))
        if not matches:
            continue
        found.add(matches[-1].group(1).strip())
    return found


def resolve_alsa_hw_path(
    path: str,
    *,
    cards: dict[str, int] | None = None,
) -> tuple[int, int] | None:
    """Map ``hw:C,D`` / ``hw:Name,D`` to ``(card, device)``."""
    cleaned = path.strip()
    if cleaned.startswith("~"):
        # Regex match rules are not resolved here (Asahi hide-parent uses exact paths).
        return None
    match = _HW_PATH.match(cleaned)
    if match is None:
        return None
    card_part = match.group(1).strip()
    device = int(match.group(2)) if match.group(2) is not None else 0
    if card_part.isdigit():
        return int(card_part), device
    names = cards if cards is not None else _alsa_card_name_to_index()
    card = names.get(card_part)
    if card is None:
        # ALSA id in brackets can be shorter than api.alsa.path card name; try prefix.
        for name, index in names.items():
            if name == card_part or card_part.startswith(name) or name.startswith(card_part):
                return index, device
        return None
    return card, device


def _playback_devices_on_card(card: int) -> set[tuple[int, int]]:
    from tunes_player.platform.linux.alsa_mixer import (
        alsa_card_from_endpoint_id,
        alsa_device_from_endpoint_id,
    )
    from tunes_player.platform.linux.audio_probe import list_alsa_playback_endpoints

    found: set[tuple[int, int]] = set()
    for endpoint_id, _mpv, _desc in list_alsa_playback_endpoints():
        c = alsa_card_from_endpoint_id(endpoint_id)
        d = alsa_device_from_endpoint_id(endpoint_id)
        if c == card and d is not None:
            found.add((c, d))
    return found


def _wireplumber_conf_dirs() -> list[Path]:
    dirs = [
        Path("/usr/share/wireplumber/wireplumber.conf.d"),
        Path("/etc/wireplumber/wireplumber.conf.d"),
    ]
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        dirs.append(Path(xdg) / "wireplumber" / "wireplumber.conf.d")
    else:
        home = os.environ.get("HOME")
        if home:
            dirs.append(Path(home) / ".config" / "wireplumber" / "wireplumber.conf.d")
    return dirs


def _alsa_card_name_to_index() -> dict[str, int]:
    try:
        text = Path("/proc/asound/cards").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return parse_asound_cards(text)


def parse_asound_cards(text: str) -> dict[str, int]:
    """Parse ``/proc/asound/cards`` into ALSA id → card index."""
    mapping: dict[str, int] = {}
    for line in text.splitlines():
        match = _CARD_LINE.match(line)
        if match is None:
            continue
        mapping[match.group(2).strip()] = int(match.group(1))
    return mapping
