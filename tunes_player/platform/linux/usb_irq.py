"""Locate USB host-controller IRQ CPUs for co-locating mpv (portable, no RT)."""

from __future__ import annotations

import logging
import re
from pathlib import Path

LOG = logging.getLogger(__name__)

_XHCI_IRQ = re.compile(r"xhci", re.IGNORECASE)
_IRQ_LINE = re.compile(r"^\s*(\d+):\s+([\d\s]+)\s+(.+)$")
_AFFINITY_ISOLATED: dict[int, int] = {}


def clear_irq_affinity_cache() -> None:
    _AFFINITY_ISOLATED.clear()


def _cpu_count() -> int:
    try:
        return len(Path("/proc/cpuinfo").read_text(encoding="utf-8").split("processor\t:"))
    except OSError:
        import os

        return os.cpu_count() or 1


def _parse_interrupts(text: str) -> list[tuple[int, tuple[int, ...], str]]:
    rows: list[tuple[int, tuple[int, ...], str]] = []
    for line in text.splitlines():
        match = _IRQ_LINE.match(line)
        if match is None:
            continue
        irq = int(match.group(1))
        counts = tuple(int(part) for part in match.group(2).split())
        label = match.group(3).strip()
        rows.append((irq, counts, label))
    return rows


def xhci_irq_cpu() -> int | None:
    """Return the CPU that handles the most xHCI interrupts, if any."""
    try:
        text = Path("/proc/interrupts").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    cpu_count = _cpu_count()
    best_cpu: int | None = None
    best_total = -1
    for _irq, counts, label in _parse_interrupts(text):
        if _XHCI_IRQ.search(label) is None:
            continue
        trimmed = counts[:cpu_count]
        if not trimmed:
            continue
        for cpu, total in enumerate(trimmed):
            if total > best_total:
                best_total = total
                best_cpu = cpu
    return best_cpu


def xhci_irq_numbers() -> list[int]:
    try:
        text = Path("/proc/interrupts").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [
        irq
        for irq, _counts, label in _parse_interrupts(text)
        if _XHCI_IRQ.search(label) is not None
    ]


def try_isolate_xhci_irq_to_cpu(cpu: int, *, force: bool = False) -> bool:
    """Best-effort: steer xHCI IRQs to *cpu* (may require privileges)."""
    if not force and cpu in _AFFINITY_ISOLATED and _AFFINITY_ISOLATED[cpu] == cpu:
        return True
    ok = True
    for irq in xhci_irq_numbers():
        affinity_path = Path(f"/proc/irq/{irq}/smp_affinity_list")
        try:
            affinity_path.write_text(f"{cpu}\n", encoding="utf-8")
            LOG.info("Set xHCI IRQ %d affinity to CPU %d", irq, cpu)
        except OSError as exc:
            LOG.debug("Could not set IRQ %d affinity to CPU %d: %s", irq, cpu, exc)
            ok = False
    _AFFINITY_ISOLATED[cpu] = cpu if ok else -1
    return ok


def preferred_playback_cpu_for_usb_card(card: int, *, force_irq: bool = False) -> int | None:
    """Pick a CPU near USB interrupt handling for this ALSA card."""
    del card  # ALSA card→xHCI mapping varies; xHCI CPU is the best portable hint.
    cpu = xhci_irq_cpu()
    if cpu is not None:
        try_isolate_xhci_irq_to_cpu(cpu, force=force_irq)
    return cpu
