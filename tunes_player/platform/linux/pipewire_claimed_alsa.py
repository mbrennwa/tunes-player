"""Detect ALSA PCMs exported into the PipeWire graph.

Historically used to omit every claimed ``alsa:hw:C:D`` twin from the bit-perfect
list. Listing now omits only software-DSP hide-parent parents (see
``pipewire_hidden_parent_alsa``); this module remains for ownership introspection
and tests. ``Audio/Device`` card-level nodes expand to every playback PCM on that
card (via ``aplay -l``).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess

log = logging.getLogger(__name__)

# wpctl inspect / pw-cli info mark some props with a leading "*".
_PROP = re.compile(
    r'^\s*\*?\s*(?P<key>[A-Za-z0-9._-]+)\s*=\s*"?(?P<val>[^"=]*?)"?\s*$'
)
_NODE_HEADER = re.compile(r"^\s*id\s+(\d+),")
_HW_NUMERIC = re.compile(r"^(?:plug)?hw:(\d+),(\d+)$", re.IGNORECASE)
_HW_NAMED = re.compile(r"^(?:plug)?hw:[^,]+,(\d+)$", re.IGNORECASE)
_HW_CARD_ONLY = re.compile(r"^(?:plug)?hw:(\d+)$", re.IGNORECASE)


def pipewire_claimed_alsa_pcms() -> set[tuple[int, int]]:
    """Return ``(card, device)`` pairs PipeWire currently owns.

    Prefer ``pw-dump`` (full props). Fail open (empty set) when PipeWire tools are
    missing or the daemon is down so USB ALSA endpoints still list.
    """
    dumped = _run_pw_dump()
    if dumped is not None:
        return parse_claimed_alsa_pcms_from_pwdump(dumped)
    text = _run_pw_cli_node_infos()
    if text is not None:
        return parse_claimed_alsa_pcms(text)
    return set()


def parse_claimed_alsa_pcms_from_pwdump(stdout: str) -> set[tuple[int, int]]:
    """Parse ``pw-dump`` JSON into claimed ``(card, device)`` pairs."""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return set()
    claimed: set[tuple[int, int]] = set()
    whole_cards: set[int] = set()
    for obj in data:
        if not isinstance(obj, dict):
            continue
        info = obj.get("info")
        if not isinstance(info, dict):
            continue
        props = info.get("props")
        if not isinstance(props, dict):
            continue
        card = _as_int(props.get("alsa.card"))
        device = _as_int(props.get("alsa.device"))
        path = props.get("api.alsa.path")
        path_s = path.strip() if isinstance(path, str) else None
        pcm = _pcm_from_props(card=card, device=device, path=path_s)
        if pcm is not None:
            claimed.add(pcm)
            continue
        whole = _whole_card_from_props(card=card, device=device, path=path_s)
        if whole is not None:
            whole_cards.add(whole)
    for card in whole_cards:
        claimed.update(_playback_devices_on_card(card))
    return claimed


def parse_claimed_alsa_pcms(stdout: str) -> set[tuple[int, int]]:
    """Parse ``pw-cli info`` / inspect-style text into claimed PCMs."""
    claimed: set[tuple[int, int]] = set()
    whole_cards: set[int] = set()
    card: int | None = None
    device: int | None = None
    path: str | None = None
    in_node = False

    def flush() -> None:
        nonlocal card, device, path, in_node
        if not in_node:
            return
        pcm = _pcm_from_props(card=card, device=device, path=path)
        if pcm is not None:
            claimed.add(pcm)
        else:
            whole = _whole_card_from_props(card=card, device=device, path=path)
            if whole is not None:
                whole_cards.add(whole)
        card = None
        device = None
        path = None
        in_node = False

    for line in stdout.splitlines():
        if _NODE_HEADER.match(line):
            flush()
            in_node = True
            continue
        if not in_node:
            continue
        match = _PROP.match(line)
        if match is None:
            continue
        key = match.group("key")
        val = match.group("val").strip().strip('"')
        if key == "alsa.card":
            card = _as_int(val)
        elif key == "alsa.device":
            device = _as_int(val)
        elif key == "api.alsa.path":
            path = val
    flush()
    for card_id in whole_cards:
        claimed.update(_playback_devices_on_card(card_id))
    return claimed


def _run_pw_dump() -> str | None:
    if shutil.which("pw-dump") is None:
        return None
    try:
        result = subprocess.run(
            ["pw-dump"],
            check=True,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        log.debug("pw-dump unavailable for claimed ALSA scan: %s", exc)
        return None
    return result.stdout


def _run_pw_cli_node_infos() -> str | None:
    """Fallback: ``pw-cli ls Node`` ids + ``pw-cli info`` for each."""
    if shutil.which("pw-cli") is None:
        return None
    try:
        listed = subprocess.run(
            ["pw-cli", "ls", "Node"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        log.debug("pw-cli ls Node unavailable for claimed ALSA scan: %s", exc)
        return None
    chunks: list[str] = []
    for line in listed.stdout.splitlines():
        header = _NODE_HEADER.match(line)
        if header is None:
            continue
        node_id = header.group(1)
        try:
            info = subprocess.run(
                ["pw-cli", "info", node_id],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
        chunks.append(f"id {node_id}, type PipeWire:Interface:Node\n{info.stdout}")
    return "\n".join(chunks) if chunks else None


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


def _pcm_from_props(
    *,
    card: int | None,
    device: int | None,
    path: str | None,
) -> tuple[int, int] | None:
    if card is not None and device is not None:
        return card, device
    if not path:
        return None
    numeric = _HW_NUMERIC.match(path)
    if numeric:
        return int(numeric.group(1)), int(numeric.group(2))
    named = _HW_NAMED.match(path)
    if named is not None and card is not None:
        return card, int(named.group(1))
    return None


def _whole_card_from_props(
    *,
    card: int | None,
    device: int | None,
    path: str | None,
) -> int | None:
    """Card-level ALSA device nodes (no PCM device) — hide-parent parents, etc."""
    if device is not None:
        return None
    if card is not None:
        if path is None or _HW_CARD_ONLY.match(path) or "," not in path:
            return card
    if path:
        only = _HW_CARD_ONLY.match(path)
        if only:
            return int(only.group(1))
    return None


def _as_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip().strip('"')
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            return int(text)
    return None
