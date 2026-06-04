"""Suspend PipeWire streams on an ALSA card for exclusive hardware access."""

from __future__ import annotations

import atexit
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_NODE_HEADER = re.compile(r"^\s*id\s+(\d+),\s+type\s+PipeWire:Interface:Node")
_ALSA_CARD = re.compile(r'^\s*alsa\.card\s*=\s*"(\d+)"')
_MEDIA_CLASS = re.compile(r'^\s*media\.class\s*=\s*"([^"]+)"')


@dataclass
class ExclusiveSession:
    """Suspend other PipeWire nodes on a card; resume on release."""

    card: int
    _suspended_ids: list[int] = field(default_factory=list)
    _released: bool = False

    def acquire(self) -> bool:
        if shutil.which("pw-cli") is None:
            log.warning("pw-cli not found; cannot suspend PipeWire streams")
            return False
        try:
            result = subprocess.run(
                ["pw-cli", "ls", "Node"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            log.warning("pw-cli ls Node failed: %s", exc)
            return False

        for node_id, node_card, media_class in _iter_nodes(result.stdout):
            if node_card != self.card:
                continue
            if media_class not in ("Audio/Sink", "Audio/Source"):
                continue
            if self._set_suspend(node_id, suspended=True):
                self._suspended_ids.append(node_id)
        return True

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        for node_id in reversed(self._suspended_ids):
            self._set_suspend(node_id, suspended=False)
        self._suspended_ids.clear()

    def __enter__(self) -> ExclusiveSession:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()

    def _set_suspend(self, node_id: int, *, suspended: bool) -> bool:
        if shutil.which("pw-cli") is None:
            return False
        state = "true" if suspended else "false"
        try:
            subprocess.run(
                [
                    "pw-cli",
                    "set-param",
                    str(node_id),
                    "PipeWire:Prop:Suspended",
                    state,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            return True
        except (OSError, subprocess.CalledProcessError) as exc:
            log.debug("pw-cli set-param Suspended on %s: %s", node_id, exc)
            return False


def _iter_nodes(stdout: str):
    node_id: int | None = None
    node_card: int | None = None
    media_class: str | None = None

    def flush():
        nonlocal node_id, node_card, media_class
        if node_id is not None and node_card is not None and media_class is not None:
            yield node_id, node_card, media_class
        node_id = None
        node_card = None
        media_class = None

    for line in stdout.splitlines():
        header = _NODE_HEADER.match(line)
        if header:
            yield from flush()
            node_id = int(header.group(1))
            continue
        if node_id is None:
            continue
        card_match = _ALSA_CARD.match(line)
        if card_match:
            node_card = int(card_match.group(1))
            continue
        class_match = _MEDIA_CLASS.match(line)
        if class_match:
            media_class = class_match.group(1)
    yield from flush()


_active_sessions: list[ExclusiveSession] = []


def acquire_exclusive_session(card: int) -> ExclusiveSession:
    session = ExclusiveSession(card=card)
    session.acquire()
    _active_sessions.append(session)
    return session


def release_exclusive_session(session: ExclusiveSession | None) -> None:
    if session is None:
        return
    session.release()
    if session in _active_sessions:
        _active_sessions.remove(session)


def release_all_exclusive_sessions() -> None:
    for session in list(_active_sessions):
        session.release()
    _active_sessions.clear()


atexit.register(release_all_exclusive_sessions)
