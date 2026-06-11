"""Serialized TIDAL stream resolution probes for catalog enrich (rate/depth labels)."""

from __future__ import annotations

import logging
import threading
import time

_log = logging.getLogger(__name__)

_PROBE_LOCK = threading.Lock()
_PROBE_CACHE: dict[int, tuple[int | None, int | None]] = {}
_PROBE_FAILURE_UNTIL: dict[int, float] = {}
_LAST_PROBE_AT = 0.0

_MIN_PROBE_INTERVAL_SEC = 0.35
_FAILURE_BACKOFF_SEC = 90.0


def clear_tidal_catalog_stream_probe_cache() -> None:
    """Test helper: drop cached probe results."""
    with _PROBE_LOCK:
        _PROBE_CACHE.clear()
        _PROBE_FAILURE_UNTIL.clear()


def peak_rate_depth_from_tidal_stream_probe(album: object) -> tuple[int | None, int | None]:
    """Peak (bit depth, sample rate Hz) via first-track stream metadata.

    Calls are serialized and spaced to avoid clashing with playback on TIDAL's
    rate-limited ``playbackinfopostpaywall`` endpoint. Results are cached per album id.
    """
    album_id = getattr(album, "id", None)
    if album_id is None or album_id == -1:
        return None, None

    numeric_id = int(album_id)
    now = time.monotonic()
    with _PROBE_LOCK:
        cached = _PROBE_CACHE.get(numeric_id)
        if cached is not None:
            return cached
        failure_until = _PROBE_FAILURE_UNTIL.get(numeric_id)
        if failure_until is not None and now < failure_until:
            return None, None

    getter = getattr(album, "get_audio_resolution", None)
    if not callable(getter):
        return None, None

    global _LAST_PROBE_AT
    with _PROBE_LOCK:
        now = time.monotonic()
        failure_until = _PROBE_FAILURE_UNTIL.get(numeric_id)
        if failure_until is not None and now < failure_until:
            return None, None
        cached = _PROBE_CACHE.get(numeric_id)
        if cached is not None:
            return cached

        wait_sec = _MIN_PROBE_INTERVAL_SEC - (now - _LAST_PROBE_AT)
        if wait_sec > 0:
            time.sleep(wait_sec)

        depth: int | None = None
        rate_hz = 0
        try:
            resolutions = getter()
            for item in resolutions or []:
                try:
                    parsed_depth = int(item[0])
                    parsed_rate = int(item[1])
                except (IndexError, TypeError, ValueError):
                    continue
                if parsed_rate > rate_hz:
                    rate_hz = parsed_rate
                    depth = parsed_depth if parsed_depth > 0 else depth
        except Exception:
            _log.debug(
                "TIDAL catalog stream probe failed for album %s",
                numeric_id,
                exc_info=True,
            )
            _PROBE_FAILURE_UNTIL[numeric_id] = time.monotonic() + _FAILURE_BACKOFF_SEC
            _LAST_PROBE_AT = time.monotonic()
            return None, None

        _LAST_PROBE_AT = time.monotonic()
        if rate_hz <= 0:
            _PROBE_FAILURE_UNTIL[numeric_id] = time.monotonic() + _FAILURE_BACKOFF_SEC
            return None, None

        if depth is None or depth <= 0:
            depth = 24 if rate_hz > 48_000 else 16
        result = (depth, rate_hz)
        _PROBE_CACHE[numeric_id] = result
        _PROBE_FAILURE_UNTIL.pop(numeric_id, None)
        return result
