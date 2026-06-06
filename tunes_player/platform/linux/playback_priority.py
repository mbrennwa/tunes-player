"""Pin mpv near USB IRQ handling — no RT limits required."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PlaybackPriorityStatus:
    pid: int
    cpu_affinity: int | None


def mpv_subprocess_command(mpv_bin: str, mpv_args: list[str]) -> tuple[list[str], bool]:
    """Return mpv argv unchanged; RT/chrt is intentionally not used (not portable)."""
    return [mpv_bin, *mpv_args], False


def pin_mpv_subprocess(
    pid: int,
    *,
    alsa_card: int | None = None,
    used_chrt: bool = False,
    force_irq: bool = False,
) -> PlaybackPriorityStatus:
    """Co-locate mpv with USB xHCI IRQ CPU when possible; otherwise leave unpinned."""
    del used_chrt
    cpu: int | None = None
    if alsa_card is not None:
        try:
            from tunes_player.platform.linux.alsa_mixer import alsa_card_is_usb
            from tunes_player.platform.linux.usb_irq import preferred_playback_cpu_for_usb_card

            if alsa_card_is_usb(alsa_card):
                cpu = preferred_playback_cpu_for_usb_card(alsa_card, force_irq=force_irq)
        except ImportError:
            cpu = None

    affinity: int | None = None
    if cpu is not None and hasattr(os, "sched_setaffinity"):
        try:
            os.sched_setaffinity(pid, {cpu})
            affinity = cpu
            LOG.info("Pinned mpv subprocess to CPU %d near USB IRQ (pid %d)", cpu, pid)
        except OSError as exc:
            LOG.debug("Could not pin mpv subprocess to CPU %d: %s", cpu, exc)
    else:
        LOG.debug("Leaving mpv subprocess scheduler placement unchanged (pid %d)", pid)

    try:
        os.setpriority(os.PRIO_PROCESS, pid, -5)
        LOG.debug("Raised mpv subprocess nice (pid %d)", pid)
    except OSError:
        LOG.debug("Could not raise mpv subprocess nice (pid %d)", pid, exc_info=True)

    return PlaybackPriorityStatus(pid=pid, cpu_affinity=affinity)


def refresh_usb_mpv_affinity(pid: int, alsa_card: int) -> PlaybackPriorityStatus:
    """Re-apply USB IRQ isolation before each track (affinity can drift under load)."""
    return pin_mpv_subprocess(pid, alsa_card=alsa_card, force_irq=True)


def raise_for_playback() -> None:
    """No-op: priority tweaks apply to the mpv child only."""


def restore_after_playback() -> None:
    """No-op."""
