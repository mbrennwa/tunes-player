"""Release-grid rebuild tracing for issue #75.

Off by default. Enable with TUNES_GRID_TRACE=1 (or true/yes/on).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence

_LOG = logging.getLogger(__name__)

_TRUE = frozenset({"1", "true", "yes", "on"})


def grid_trace_enabled() -> bool:
    """Return whether grid rebuild tracing is active.

    Default: off. Explicit TUNES_GRID_TRACE=1/true/yes/on enables it.
    """
    raw = os.environ.get("TUNES_GRID_TRACE")
    if raw is None or not raw.strip():
        return False
    return raw.strip().lower() in _TRUE


def _ids_preview(ids: Sequence[str], *, head: int = 3, tail: int = 2) -> str:
    if not ids:
        return "[]"
    if len(ids) <= head + tail:
        return "[" + ", ".join(ids) + "]"
    front = ", ".join(ids[:head])
    back = ", ".join(ids[-tail:])
    return f"[{front}, …(+{len(ids) - head - tail}), {back}]"


def _fingerprint_delta(
    old: tuple[str, tuple[str, ...], str | None] | None,
    new: tuple[str, tuple[str, ...], str | None],
) -> str:
    if old is None:
        return "prev=none"
    old_title, old_ids, old_empty = old
    new_title, new_ids, new_empty = new
    parts: list[str] = []
    if old_title != new_title:
        parts.append(f"title:{old_title!r}->{new_title!r}")
    if old_ids != new_ids:
        if set(old_ids) == set(new_ids) and len(old_ids) == len(new_ids):
            parts.append(f"ids:same-set-reordered n={len(new_ids)}")
        else:
            parts.append(
                f"ids:n {len(old_ids)}->{len(new_ids)} "
                f"old={_ids_preview(old_ids)} new={_ids_preview(new_ids)}"
            )
    if old_empty != new_empty:
        parts.append(f"empty_message:{old_empty!r}->{new_empty!r}")
    return "unchanged" if not parts else "; ".join(parts)


def log_grid_event(event: str, *, reason: str, **fields: object) -> None:
    """Emit one INFO line when grid tracing is enabled."""
    if not grid_trace_enabled():
        return
    extras = " ".join(f"{key}={value!r}" for key, value in fields.items() if value is not None)
    if extras:
        _LOG.info("grid_trace %s reason=%s %s", event, reason, extras)
    else:
        _LOG.info("grid_trace %s reason=%s", event, reason)


def log_show_grid_decision(
    *,
    reason: str,
    action: str,
    fingerprint: tuple[str, tuple[str, ...], str | None],
    previous: tuple[str, tuple[str, ...], str | None] | None,
    at_root: bool,
    on_release_grid: bool,
) -> None:
    """Log skip vs recreate for `_show_grid`."""
    if not grid_trace_enabled():
        return
    title, ids, empty_message = fingerprint
    same_ids = previous is not None and previous[1] == ids
    # Same visible release IDs but still recreating the grid — the tear-down
    # symptom reported in #75 (fingerprint may have been cleared, or title/empty
    # message differs while the album set is unchanged).
    spurious = action == "recreate" and same_ids
    _LOG.info(
        "grid_trace show_grid action=%s reason=%s spurious_rebuild=%s "
        "at_root=%s on_release_grid=%s n=%d title=%r empty=%r delta=%s ids=%s",
        action,
        reason,
        spurious,
        at_root,
        on_release_grid,
        len(ids),
        title,
        empty_message,
        _fingerprint_delta(previous, fingerprint),
        _ids_preview(ids),
    )
