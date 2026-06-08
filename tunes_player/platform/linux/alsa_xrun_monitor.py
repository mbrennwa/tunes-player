"""Poll ALSA PCM status for xrun counter increases (Linux direct output).

Diagnostics only — logs ALSA XRUNs; does not trigger playback recovery (#46).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

LOG = logging.getLogger(__name__)

_STATE_RE = re.compile(r"^state:\s*(\S+)")
_XRUNS_RE = re.compile(r"^xruns:\s*(\d+)")
_CARD_FROM_MPV_DEVICE = re.compile(r"(?:hw|plughw):(\d+)", re.IGNORECASE)
_CARD_FROM_ENDPOINT = re.compile(r"^alsa:(\d+)", re.IGNORECASE)

@dataclass(frozen=True, slots=True)
class PcmStatus:
    path: str
    state: str
    xruns: int | None


def parse_card_from_mpv_device(device: str | None) -> int | None:
    if not device:
        return None
    match = _CARD_FROM_MPV_DEVICE.search(device)
    if match is None:
        return None
    return int(match.group(1))


def parse_card_from_endpoint_id(endpoint_id: str | None) -> int | None:
    if not endpoint_id:
        return None
    match = _CARD_FROM_ENDPOINT.match(endpoint_id)
    if match is None:
        return None
    return int(match.group(1))


def parse_pcm_status(text: str, *, path: str = "") -> PcmStatus:
    state = "UNKNOWN"
    xruns: int | None = None
    for line in text.splitlines():
        state_match = _STATE_RE.match(line)
        if state_match is not None:
            state = state_match.group(1)
            continue
        xruns_match = _XRUNS_RE.match(line)
        if xruns_match is not None:
            xruns = int(xruns_match.group(1))
    return PcmStatus(path=path, state=state, xruns=xruns)


def list_playback_pcm_statuses(*, card: int | None = None) -> list[PcmStatus]:
    statuses: list[PcmStatus] = []
    for path in sorted(Path("/proc/asound").glob("card*/pcm*p/sub*/status")):
        if card is not None:
            card_dir = path.parts[path.parts.index("asound") + 1]
            if not card_dir.startswith(f"card{card}"):
                continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        statuses.append(parse_pcm_status(text, path=str(path)))
    return statuses


class AlsaXrunMonitor:
    """Log ALSA playback PCM xrun events for the active output card."""

    def __init__(self) -> None:
        self._last_xruns: dict[str, int] = {}
        self._last_state: dict[str, str] = {}
        self._card: int | None = None

    def reset(self) -> None:
        self._last_xruns.clear()
        self._last_state.clear()
        self._card = None

    def set_card(self, card: int | None) -> None:
        if card != self._card:
            self._last_xruns.clear()
            self._last_state.clear()
            self._card = card

    def poll(
        self,
        *,
        mpv_audio_device: str | None = None,
        endpoint_id: str | None = None,
    ) -> None:
        """Poll PCM status and log new xrun or XRUN state transitions."""
        card = parse_card_from_mpv_device(mpv_audio_device)
        if card is None:
            card = parse_card_from_endpoint_id(endpoint_id)
        self.set_card(card)

        for status in list_playback_pcm_statuses(card=self._card):
            path = status.path
            prev_state = self._last_state.get(path)
            if status.state == "XRUN" and prev_state != "XRUN":
                LOG.warning(
                    "ALSA PCM entered XRUN state (%s, xruns=%s)",
                    path,
                    status.xruns if status.xruns is not None else "?",
                )
            self._last_state[path] = status.state

            if status.xruns is None:
                continue
            previous = self._last_xruns.get(path)
            if previous is not None and status.xruns > previous:
                LOG.warning(
                    "ALSA xrun counter increased on %s: %d -> %d (state=%s)",
                    path,
                    previous,
                    status.xruns,
                    status.state,
                )
            self._last_xruns[path] = status.xruns
