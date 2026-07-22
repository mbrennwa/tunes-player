"""Poll ALSA PCM status for xruns and stalled hardware/app pointers.

Used by ``scripts/engine_stress_benchmark.py`` and the optional playback health
monitor (``TUNES_PLAYBACK_HEALTH_LOG``) for direct-ALSA feed detection (#67).
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

LOG = logging.getLogger(__name__)

_STATE_RE = re.compile(r"^state:\s*(\S+)")
_XRUNS_RE = re.compile(r"^xruns:\s*(\d+)")
_HW_PTR_RE = re.compile(r"^hw_ptr\s*:\s*(\d+)")
_APPL_PTR_RE = re.compile(r"^appl_ptr\s*:\s*(\d+)")
_DELAY_RE = re.compile(r"^delay\s*:\s*(-?\d+)")
_AVAIL_RE = re.compile(r"^avail\s*:\s*(-?\d+)")
_CARD_FROM_MPV_DEVICE = re.compile(r"(?:hw|plughw):(\d+)", re.IGNORECASE)
_CARD_FROM_ENDPOINT = re.compile(
    r"^alsa:(?:hw:)?(\d+)",
    re.IGNORECASE,
)

# Minimum pointer advance between polls to count as "feeding" the device.
_MIN_PTR_DELTA = 1


@dataclass(frozen=True, slots=True)
class PcmStatus:
    path: str
    state: str
    xruns: int | None
    hw_ptr: int | None = None
    appl_ptr: int | None = None
    delay: int | None = None
    avail: int | None = None


@dataclass(frozen=True, slots=True)
class AlsaFeedIssue:
    """Feed/device problem observed while polling ALSA PCM status."""

    code: str
    message: str


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
    hw_ptr: int | None = None
    appl_ptr: int | None = None
    delay: int | None = None
    avail: int | None = None
    for line in text.splitlines():
        state_match = _STATE_RE.match(line)
        if state_match is not None:
            state = state_match.group(1)
            continue
        xruns_match = _XRUNS_RE.match(line)
        if xruns_match is not None:
            xruns = int(xruns_match.group(1))
            continue
        hw_match = _HW_PTR_RE.match(line)
        if hw_match is not None:
            hw_ptr = int(hw_match.group(1))
            continue
        appl_match = _APPL_PTR_RE.match(line)
        if appl_match is not None:
            appl_ptr = int(appl_match.group(1))
            continue
        delay_match = _DELAY_RE.match(line)
        if delay_match is not None:
            delay = int(delay_match.group(1))
            continue
        avail_match = _AVAIL_RE.match(line)
        if avail_match is not None:
            avail = int(avail_match.group(1))
    return PcmStatus(
        path=path,
        state=state,
        xruns=xruns,
        hw_ptr=hw_ptr,
        appl_ptr=appl_ptr,
        delay=delay,
        avail=avail,
    )


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


def pointer_delta(previous: int, current: int) -> int:
    """Unsigned-ish advance of an ALSA ring pointer (handles wrap)."""
    if current >= previous:
        return current - previous
    # Wrapped: any decrease still means the device kept moving.
    return 1


class AlsaXrunMonitor:
    """Log ALSA playback PCM xruns and detect stalled feed pointers."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last_xruns: dict[str, int] = {}
        self._last_state: dict[str, str] = {}
        self._last_hw_ptr: dict[str, int] = {}
        self._last_appl_ptr: dict[str, int] = {}
        self._last_ptr_at: dict[str, float] = {}
        self._card: int | None = None

    def reset(self) -> None:
        self._last_xruns.clear()
        self._last_state.clear()
        self._last_hw_ptr.clear()
        self._last_appl_ptr.clear()
        self._last_ptr_at.clear()
        self._card = None

    def set_card(self, card: int | None) -> None:
        if card != self._card:
            self._last_xruns.clear()
            self._last_state.clear()
            self._last_hw_ptr.clear()
            self._last_appl_ptr.clear()
            self._last_ptr_at.clear()
            self._card = card

    def poll(
        self,
        *,
        mpv_audio_device: str | None = None,
        endpoint_id: str | None = None,
        expect_feeding: bool = False,
    ) -> list[AlsaFeedIssue]:
        """Poll PCM status; log xruns; optionally return feed-stall issues."""
        card = parse_card_from_mpv_device(mpv_audio_device)
        if card is None:
            card = parse_card_from_endpoint_id(endpoint_id)
        self.set_card(card)

        issues: list[AlsaFeedIssue] = []
        statuses = list_playback_pcm_statuses(card=self._card)
        if expect_feeding and self._card is not None and not statuses:
            issues.append(
                AlsaFeedIssue(
                    code="alsa_pcm_missing",
                    message=f"no ALSA playback PCM status for card{self._card}",
                )
            )

        now = self._clock()
        for status in statuses:
            path = status.path
            prev_state = self._last_state.get(path)
            if status.state == "XRUN" and prev_state != "XRUN":
                LOG.warning(
                    "ALSA PCM entered XRUN state (%s, xruns=%s)",
                    path,
                    status.xruns if status.xruns is not None else "?",
                )
            self._last_state[path] = status.state

            if status.xruns is not None:
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

            if not expect_feeding:
                continue

            if status.state not in ("RUNNING", "XRUN", "DRAINING"):
                issues.append(
                    AlsaFeedIssue(
                        code="alsa_not_running",
                        message=(
                            f"ALSA PCM state={status.state} "
                            f"(expected RUNNING) path={path}"
                        ),
                    )
                )
                # Reset pointers so the next RUNNING sample becomes a baseline.
                self._last_hw_ptr.pop(path, None)
                self._last_appl_ptr.pop(path, None)
                self._last_ptr_at.pop(path, None)
                continue

            hw_prev = self._last_hw_ptr.get(path)
            appl_prev = self._last_appl_ptr.get(path)
            ptr_at = self._last_ptr_at.get(path)

            hw_delta = 0
            appl_delta = 0
            if status.hw_ptr is not None and hw_prev is not None:
                hw_delta = pointer_delta(hw_prev, status.hw_ptr)
            if status.appl_ptr is not None and appl_prev is not None:
                appl_delta = pointer_delta(appl_prev, status.appl_ptr)

            if status.hw_ptr is not None:
                self._last_hw_ptr[path] = status.hw_ptr
            if status.appl_ptr is not None:
                self._last_appl_ptr[path] = status.appl_ptr
            self._last_ptr_at[path] = now

            # Need a prior sample before judging stall.
            if hw_prev is None and appl_prev is None:
                continue
            if ptr_at is not None and now - ptr_at < 0.2:
                continue

            if hw_delta < _MIN_PTR_DELTA and appl_delta < _MIN_PTR_DELTA:
                issues.append(
                    AlsaFeedIssue(
                        code="alsa_feed_stalled",
                        message=(
                            f"ALSA PCM pointers not advancing "
                            f"(hw_ptr={status.hw_ptr} appl_ptr={status.appl_ptr} "
                            f"delay={status.delay} avail={status.avail} "
                            f"state={status.state}) path={path}"
                        ),
                    )
                )

        return issues
