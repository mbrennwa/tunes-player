"""mpv JSON IPC helpers (pure functions for tests)."""

from __future__ import annotations

# mpv client.h mpv_end_file_reason (numeric JSON IPC values).
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
