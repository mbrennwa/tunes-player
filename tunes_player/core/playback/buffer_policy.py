"""mpv buffering policy for jitter-prone playback input (network files, streaming)."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from urllib.parse import unquote, urlparse

# LAN NAS / streaming: modest demuxer cache for brief NFS or HTTPS stalls.
_NETWORK_DEMUXER_READAHEAD_SEC = 8.0
_NETWORK_CACHE_SEC = 30.0
_NETWORK_DEMUXER_MAX_BYTES = 64 * 1024 * 1024
_LOCAL_DEMUXER_READAHEAD_SEC = 1.0
# Direct ALSA bit-perfect: large hardware buffer for USB jitter; modest decoder
# queue for AAC decode. Do not set mpv audio-buffer to multi-second values —
# that desyncs playlist-pos from audible playback (see mpv manual).
_DIRECT_ALSA_ALSA_BUFFER_TIME_US = 10_000_000
_DIRECT_ALSA_ALSA_PERIODS = 8
_DIRECT_ALSA_AD_QUEUE_MAX_SEC = 4.0
# Direct ALSA + mpv software volume: mpv attenuates before the AO ring buffer.
# Multi-second ALSA/ad queues make audible level lag the UI slider by seconds.
_DIRECT_ALSA_SOFTVOL_ALSA_BUFFER_TIME_US = 500_000
_DIRECT_ALSA_SOFTVOL_ALSA_PERIODS = 4
_DIRECT_ALSA_SOFTVOL_AD_QUEUE_MAX_SEC = 0.25
_DIRECT_ALSA_CACHE_PAUSE_WAIT_SEC = 1.0
_DIRECT_ALSA_DEMUXER_READAHEAD_SEC = 8.0


class InputClass(str, Enum):
    LOCAL = "local"
    NETWORK_FILE = "network_file"
    STREAM = "stream"


def classify_playback_uri(uri: str) -> InputClass:
    """Classify a mpv load target (filesystem path or URL)."""
    if uri.startswith(("http://", "https://")):
        return InputClass.STREAM
    if uri.startswith("file://"):
        parsed = urlparse(uri)
        path = Path(unquote(parsed.path))
    else:
        path = Path(uri)
    try:
        from tunes_player.platform.linux.mount_info import is_network_mount_path

        if is_network_mount_path(path):
            return InputClass.NETWORK_FILE
    except ImportError:
        pass
    return InputClass.LOCAL


def mpv_options_for_input(
    input_class: InputClass,
    *,
    direct_alsa: bool,
    warmup: bool = True,
    software_volume: bool = False,
) -> dict[str, object]:
    """Return mpv properties to apply before loading a track."""
    if input_class in (InputClass.NETWORK_FILE, InputClass.STREAM):
        options: dict[str, object] = {
            "demuxer_readahead_secs": _NETWORK_DEMUXER_READAHEAD_SEC,
            "demuxer_max_bytes": _NETWORK_DEMUXER_MAX_BYTES,
            "cache": "yes",
            "cache_secs": _NETWORK_CACHE_SEC,
        }
    else:
        options = {
            "demuxer_readahead_secs": _LOCAL_DEMUXER_READAHEAD_SEC,
            "cache": "no",
        }
    if direct_alsa:
        if software_volume:
            options["alsa_buffer_time"] = _DIRECT_ALSA_SOFTVOL_ALSA_BUFFER_TIME_US
            options["alsa_periods"] = _DIRECT_ALSA_SOFTVOL_ALSA_PERIODS
            options["ad_queue_max_secs"] = _DIRECT_ALSA_SOFTVOL_AD_QUEUE_MAX_SEC
        else:
            options["alsa_buffer_time"] = _DIRECT_ALSA_ALSA_BUFFER_TIME_US
            options["alsa_periods"] = _DIRECT_ALSA_ALSA_PERIODS
            options["ad_queue_max_secs"] = _DIRECT_ALSA_AD_QUEUE_MAX_SEC
        options["demuxer_thread"] = "yes"
        options["ad_lavc_threads"] = 1
        options["ad_queue_enable"] = "yes"
        if input_class == InputClass.LOCAL:
            # Staged/local files: large ao/ALSA buffers only — demuxer cache adds CPU
            # and background I/O that competes with USB isochronous output.
            options["cache"] = "no"
            options["cache_pause"] = "no"
        else:
            options["cache_pause"] = "yes"
            if warmup:
                options["cache_pause_initial"] = "yes"
                options["cache_pause_wait"] = _DIRECT_ALSA_CACHE_PAUSE_WAIT_SEC
            else:
                options["cache_pause_initial"] = "no"
            options["cache"] = "yes"
            options["cache_secs"] = _NETWORK_CACHE_SEC
            options["demuxer_max_bytes"] = _NETWORK_DEMUXER_MAX_BYTES
            readahead = float(options.get("demuxer_readahead_secs", _LOCAL_DEMUXER_READAHEAD_SEC))
            options["demuxer_readahead_secs"] = max(
                readahead,
                _DIRECT_ALSA_DEMUXER_READAHEAD_SEC,
            )
    return options


def direct_alsa_engine_options(
    *,
    warmup: bool = True,
    software_volume: bool = False,
) -> dict[str, object]:
    """mpv init options for direct ALSA before the output device first opens."""
    return mpv_options_for_input(
        InputClass.LOCAL,
        direct_alsa=True,
        warmup=warmup,
        software_volume=software_volume,
    )


def buffered_playback_note(input_class: InputClass) -> str | None:
    if input_class == InputClass.NETWORK_FILE:
        return "Network library (buffered)"
    if input_class == InputClass.STREAM:
        return "Streaming (buffered)"
    return None


def merge_playback_note(
    base: str | None,
    input_class: InputClass,
) -> str | None:
    extra = buffered_playback_note(input_class)
    if extra is None:
        return base
    if not base:
        return extra
    if extra in base:
        return base
    return f"{base} · {extra}"
