"""Remote MPD playback engine (#60).

Tunes stays the library and queue owner; MPD (on another host) decodes and
outputs. The engine keeps a single-entry MPD queue and mirrors MPD's state back
as the same :data:`EngineEvent` stream ``MpvEngine`` produces. Everything behind
MPD (ALSA routing, DSP, sample-rate handling, mixer) is configured outside Tunes.

State is learned by polling ``status`` from a background thread; the cheap
accessors (``get_position`` and friends) read a cache so the UI thread never
waits on the network.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from tunes_player.core.playback.engine import EngineEvent
from tunes_player.core.playback.mpd_client import (
    MpdClient,
    MpdCommandError,
    MpdConnectionError,
    MpdError,
    parse_status_time,
)

if TYPE_CHECKING:
    from tunes_player.core.playback.output_profile import PlaybackOutputProfile

EngineCallback = Callable[[EngineEvent], None]

_LOG = logging.getLogger(__name__)

POLL_INTERVAL_PLAYING_SEC = 0.5
POLL_INTERVAL_IDLE_SEC = 1.0
# Same margin as MpvEngine: keep seeks away from the very end of the track.
_SEEK_END_MARGIN_SEC = 1.0
# While MPD reports "play", never report a position within this margin of the
# end. The queue's poll-based auto-advance fires at duration - 1 s; staying
# below that lets MPD's own end-of-track (``track_eof``) drive the advance so
# the last second of a track is not cut off.
_EOF_POSITION_GUARD_SEC = 1.5


class MpdEngine:
    """PlaybackEngine implementation that drives a remote MPD server."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        name: str | None = None,
        on_event: EngineCallback | None = None,
        client_factory: Callable[[str, int], MpdClient] | None = None,
        poll_playing_sec: float = POLL_INTERVAL_PLAYING_SEC,
        poll_idle_sec: float = POLL_INTERVAL_IDLE_SEC,
    ) -> None:
        self.host = host
        self.port = port
        self.name = name or f"{host}:{port}"
        self._on_event = on_event
        self._poll_playing_sec = poll_playing_sec
        self._poll_idle_sec = poll_idle_sec
        factory = client_factory or (lambda h, p: MpdClient(h, p))
        self._client = factory(host, port)
        self._poll_client = factory(host, port)

        self._lock = threading.RLock()
        self._shutdown = threading.Event()
        self._generation = 0
        self._loaded_uri: str | None = None
        self._playing = False
        self._duration_sec: float | None = None
        self._pos_base = 0.0
        self._pos_clock = time.monotonic()
        self._track_end_signaled = False
        self._reported_error: str | None = None
        self._connection_lost = False
        self._load_in_progress = False
        self._mpd_state = "stop"
        self._last_error_text: str | None = None

        try:
            self._client.connect()
        except MpdConnectionError as exc:
            raise RuntimeError(
                f"Could not connect to MPD '{self.name}' at {host}:{port}. "
                "Check that MPD is running and reachable from this computer."
            ) from exc

        self._thread = threading.Thread(
            target=self._poll_loop, name="tunes-mpd-poll", daemon=True
        )
        self._thread.start()

    # -- lifecycle -----------------------------------------------------

    def is_available(self) -> bool:
        return not self._shutdown.is_set()

    @property
    def load_in_progress(self) -> bool:
        return self._load_in_progress

    def set_event_callback(self, callback: EngineCallback | None) -> None:
        self._on_event = callback  # type: ignore[assignment]

    def quit(self) -> None:
        if self._shutdown.is_set():
            return
        self._shutdown.set()
        with self._lock:
            self._generation += 1
            had_track = self._loaded_uri is not None
            self._loaded_uri = None
            self._playing = False
        if had_track:
            # Leaving this output (or quitting): do not leave MPD playing.
            try:
                self._client.stop()
            except MpdError:
                _LOG.debug("MPD stop on quit failed", exc_info=True)
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self._client.close()
        self._poll_client.close()

    # -- PlaybackEngine ------------------------------------------------

    def set_output_profile(self, profile: PlaybackOutputProfile | None) -> None:
        """Local output profiles do not apply to a remote MPD."""

    def set_bit_perfect(self, enabled: bool) -> None:
        """No local gain stage to configure."""

    def set_volume(self, level: float) -> None:
        """Level is set on the endpoint; Tunes never changes MPD volume."""

    def load(
        self,
        uri: str,
        *,
        start_sec: float = 0,
        output_profile: PlaybackOutputProfile | None = None,
        mode: str = "replace",
    ) -> None:
        if mode != "replace":
            raise NotImplementedError("MPD engine supports replace loads only")
        start = max(0.0, start_sec)
        with self._lock:
            self._generation += 1
            self._load_in_progress = True
        try:
            commands: list[tuple[str, tuple[object, ...]]] = [
                ("clear", ()),
                ("add", (uri,)),
            ]
            if start > 0:
                commands.append(("seek", (0, f"{start:.3f}")))
            else:
                commands.append(("play", (0,)))
            self._client.command_list(commands)
        except MpdCommandError as exc:
            self._load_in_progress = False
            raise RuntimeError(self._describe_command_error(exc)) from exc
        except MpdConnectionError:
            self._load_in_progress = False
            raise
        with self._lock:
            self._generation += 1
            self._loaded_uri = uri
            self._playing = True
            self._mpd_state = "play"
            self._duration_sec = None
            self._track_end_signaled = False
            self._reported_error = None
            self._connection_lost = False
            self._seed_position(start)
            self._load_in_progress = False
        self._emit("track_started")
        self._emit("duration_changed")
        self._emit("playing_changed")

    def play(self) -> None:
        with self._lock:
            if self._loaded_uri is None:
                return
            stopped = self._mpd_state == "stop"
            self._generation += 1
        try:
            if stopped:
                self._client.play(0)
            else:
                self._client.pause(False)
        except MpdError as exc:
            self._report_error(str(exc))
            return
        with self._lock:
            self._playing = True
            self._mpd_state = "play"
            self._reported_error = None
            self._touch_clock()
        self._emit("playing_changed")

    def pause(self) -> None:
        with self._lock:
            if self._loaded_uri is None:
                return
            self._generation += 1
            position = self._current_position()
        try:
            self._client.pause(True)
        except MpdError as exc:
            self._report_error(str(exc))
            return
        with self._lock:
            self._playing = False
            self._mpd_state = "pause"
            self._seed_position(position)
        self._emit("playing_changed")
        self._emit("position_changed")

    def stop(self) -> None:
        with self._lock:
            self._generation += 1
            self._loaded_uri = None
            self._playing = False
            self._mpd_state = "stop"
            self._duration_sec = None
            self._seed_position(0.0)
        try:
            self._client.stop()
            self._client.clear()
        except MpdError:
            _LOG.debug("MPD stop failed", exc_info=True)
        self._emit("playing_changed")
        self._emit("position_changed")
        self._emit("duration_changed")

    def max_seek_position_sec(self) -> float | None:
        duration = self._duration_sec
        if duration is None or duration <= 0:
            return None
        return max(0.0, duration - _SEEK_END_MARGIN_SEC)

    def seek(self, position_sec: float, *, resume: bool | None = None) -> None:
        with self._lock:
            if self._loaded_uri is None:
                return
            should_resume = self._playing if resume is None else resume
            target = max(0.0, position_sec)
            cap = self.max_seek_position_sec()
            if cap is not None:
                target = min(target, cap)
            self._generation += 1
        try:
            self._client.seekcur(target)
            if should_resume and not self._playing:
                self._client.pause(False)
            elif resume is False and self._playing:
                self._client.pause(True)
        except MpdError as exc:
            self._report_error(str(exc))
            return
        with self._lock:
            self._seed_position(target)
            if resume is not None:
                self._playing = should_resume
                self._mpd_state = "play" if should_resume else "pause"
        self._emit("position_changed")
        if resume is not None:
            self._emit("playing_changed")

    def get_position(self) -> float:
        return self.query_time_pos()

    def query_time_pos(self) -> float:
        with self._lock:
            return self._current_position()

    def get_time_pos(self) -> float:
        return self.query_time_pos()

    def get_duration(self) -> float | None:
        return self._duration_sec

    def is_playing(self) -> bool:
        with self._lock:
            return self._loaded_uri is not None and self._playing

    # -- position cache ------------------------------------------------

    def _touch_clock(self) -> None:
        self._pos_clock = time.monotonic()

    def _seed_position(self, position_sec: float) -> None:
        self._pos_base = max(0.0, position_sec)
        self._touch_clock()

    def _current_position(self) -> float:
        position = self._pos_base
        if self._playing:
            position += time.monotonic() - self._pos_clock
            duration = self._duration_sec
            if duration is not None and duration > _EOF_POSITION_GUARD_SEC:
                position = min(position, duration - _EOF_POSITION_GUARD_SEC)
        return max(0.0, position)

    # -- status polling ------------------------------------------------

    def _poll_loop(self) -> None:
        while True:
            interval = self._poll_playing_sec if self._playing else self._poll_idle_sec
            if self._shutdown.wait(interval):
                return
            with self._lock:
                generation = self._generation
                active = self._loaded_uri is not None
            if not active:
                continue
            try:
                status = self._poll_client.status()
            except MpdConnectionError as exc:
                self._on_poll_connection_error(exc)
                continue
            except MpdError:
                _LOG.debug("MPD status failed", exc_info=True)
                continue
            self._apply_status(status, generation)

    def _on_poll_connection_error(self, exc: Exception) -> None:
        with self._lock:
            if self._connection_lost or self._loaded_uri is None:
                return
            self._connection_lost = True
            self._playing = False
            self._last_error_text = (
                f"Lost connection to MPD '{self.name}' ({self.host}:{self.port})."
            )
        _LOG.warning("Lost connection to MPD %s: %s", self.name, exc)
        self._emit("playing_changed")
        self._emit("playback_error")

    def _apply_status(self, status: dict[str, str], generation: int) -> None:
        events: list[EngineEvent] = []
        clear_error = False
        with self._lock:
            if (
                generation != self._generation
                or self._loaded_uri is None
                or self._load_in_progress
            ):
                return
            if self._connection_lost:
                self._connection_lost = False
            state = status.get("state", "stop")
            elapsed, duration = parse_status_time(status)
            error = status.get("error")

            if duration is not None and duration != self._duration_sec:
                self._duration_sec = duration
                events.append("duration_changed")

            if error:
                if error != self._reported_error:
                    self._reported_error = error
                    self._last_error_text = error
                    self._playing = False
                    self._mpd_state = state
                    events.append("playing_changed")
                    events.append("playback_error")
                clear_error = True
            elif state == "play":
                changed = not self._playing
                self._playing = True
                self._mpd_state = "play"
                self._reported_error = None
                self._pos_base = elapsed
                self._touch_clock()
                events.append("position_changed")
                if changed:
                    events.append("playing_changed")
            elif state == "pause":
                changed = self._playing
                moved = abs(elapsed - self._pos_base) > 0.01
                self._playing = False
                self._mpd_state = "pause"
                self._pos_base = elapsed
                self._touch_clock()
                if moved or changed:
                    events.append("position_changed")
                if changed:
                    events.append("playing_changed")
            elif state == "stop" and not self._track_end_signaled:
                # MPD ran out of queue without an error: natural end of track.
                self._track_end_signaled = True
                self._playing = False
                self._mpd_state = "stop"
                if self._duration_sec is not None:
                    self._pos_base = self._duration_sec
                self._touch_clock()
                events.extend(["track_eof", "position_changed", "playing_changed"])
        if clear_error:
            try:
                self._poll_client.command("clearerror")
            except MpdError:
                _LOG.debug("MPD clearerror failed", exc_info=True)
        for event in events:
            self._emit(event)

    # -- errors --------------------------------------------------------

    @property
    def last_error(self) -> str | None:
        """Most recent MPD error text (for example a busy output device)."""
        return self._last_error_text

    def _report_error(self, message: str) -> None:
        with self._lock:
            self._last_error_text = message
            self._playing = False
        self._emit("playing_changed")
        self._emit("playback_error")

    def _describe_command_error(self, exc: MpdCommandError) -> str:
        message = exc.message or str(exc)
        return f"MPD '{self.name}' could not play this track: {message}"

    def _emit(self, event: EngineEvent) -> None:
        callback = self._on_event
        if callback is None or self._shutdown.is_set():
            return
        callback(event)
