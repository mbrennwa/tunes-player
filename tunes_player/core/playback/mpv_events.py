"""mpv end-file reason helpers (shared by in-process engine and tests)."""

from __future__ import annotations

# mpv client.h mpv_end_file_reason (numeric property values).
END_FILE_EOF = 0
END_FILE_STOP = 2
END_FILE_QUIT = 3
END_FILE_REDIRECT = 4
END_FILE_ERROR = 5

# mpv also emits string reasons in end-file events on current releases.
END_FILE_REASON_EOF = "eof"
END_FILE_REASON_ERROR = "error"


def end_file_triggers_playback_error(reason: object) -> bool:
    if reason == END_FILE_REASON_ERROR:
        return True
    try:
        code = int(reason)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return code == END_FILE_ERROR


def end_file_triggers_track_finished(reason: object) -> bool:
    if reason == END_FILE_REASON_EOF:
        return True
    try:
        code = int(reason)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return code == END_FILE_EOF


def end_file_applies_to_playlist_entry(
    *,
    active_entry_id: int | None,
    event_entry_id: object,
) -> bool:
    """Ignore end-file events from a replaced or not-yet-active playlist entry."""
    if active_entry_id is None:
        return False
    if event_entry_id is None:
        return True
    try:
        return int(event_entry_id) == active_entry_id
    except (TypeError, ValueError):
        return True
