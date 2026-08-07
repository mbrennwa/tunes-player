"""Watch OS sink/mixer volume and report inbound changes (#104)."""

from __future__ import annotations

import logging
import os
import select
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Literal

log = logging.getLogger(__name__)

WatchMode = Literal["events", "poll"]

_DELTA = 1e-3
# Bound perceived slider lag; also used as the events-path backup sample period.
_DEFAULT_POLL_INTERVAL_SEC = 0.1


def pactl_subscribe_is_relevant(line: str) -> bool:
    """True when a ``pactl subscribe`` line may affect sink volume."""
    lowered = line.strip().casefold()
    if "event" not in lowered or "change" not in lowered:
        return False
    return " on sink" in lowered or " on server" in lowered


def pactl_subscribe_argv(pactl: str) -> list[str]:
    """Build argv for subscribe; prefer line-buffered stdout via stdbuf."""
    stdbuf = shutil.which("stdbuf")
    if stdbuf is not None:
        return [stdbuf, "-oL", pactl, "subscribe"]
    return [pactl, "subscribe"]


class StackVolumeWatcher:
    """Sample active endpoint volume on stack events or a poll interval."""

    def __init__(
        self,
        *,
        should_watch: Callable[[], bool],
        read_level: Callable[[], float],
        on_external: Callable[[float], None],
        watch_mode: Callable[[], WatchMode],
        poll_interval_sec: float = _DEFAULT_POLL_INTERVAL_SEC,
        pactl_path: str | None = None,
    ) -> None:
        self._should_watch = should_watch
        self._read_level = read_level
        self._on_external = on_external
        self._watch_mode = watch_mode
        self._poll_interval_sec = max(0.05, poll_interval_sec)
        self._pactl_path = pactl_path
        self._stop = threading.Event()
        self._reset = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_level: float | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="volume-stack-watch",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._kill_subscribe_proc()
        thread = self._thread
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=2.0)
        with self._lock:
            self._thread = None
            self._last_level = None

    def reset_baseline(self) -> None:
        """Forget last-seen level after endpoint / mode changes."""
        with self._lock:
            self._last_level = None
        self._reset.set()

    def note_applied_level(self, level: float) -> None:
        """Align baseline with an outbound set_level so echoes are not re-notified."""
        with self._lock:
            self._last_level = max(0.0, min(1.0, level))

    def _resolved_pactl(self) -> str | None:
        if self._pactl_path is not None:
            return self._pactl_path
        return shutil.which("pactl")

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self._should_watch():
                self._kill_subscribe_proc()
                with self._lock:
                    self._last_level = None
                self._stop.wait(self._poll_interval_sec)
                continue
            mode = self._watch_mode()
            if mode == "events" and self._resolved_pactl():
                self._run_events_until_mode_change()
            else:
                self._kill_subscribe_proc()
                self._poll_once()
                self._wait_interruptible(self._poll_interval_sec)

    def _run_events_until_mode_change(self) -> None:
        pactl = self._resolved_pactl()
        if pactl is None:
            return
        # Seed baseline so we only notify on subsequent external changes.
        self._poll_once()
        try:
            proc = subprocess.Popen(
                pactl_subscribe_argv(pactl),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
        except OSError:
            log.debug("Could not start pactl subscribe", exc_info=True)
            self._wait_interruptible(self._poll_interval_sec)
            return
        with self._lock:
            self._proc = proc
        stdout = proc.stdout
        if stdout is None:
            self._kill_subscribe_proc()
            return
        fd = stdout.fileno()
        pending = bytearray()
        try:
            while not self._stop.is_set():
                if not self._should_watch() or self._watch_mode() != "events":
                    break
                if self._reset.is_set():
                    self._reset.clear()
                    with self._lock:
                        self._last_level = None
                    self._poll_once()
                    continue
                try:
                    ready, _, _ = select.select(
                        [fd], [], [], self._poll_interval_sec
                    )
                except (OSError, ValueError):
                    break
                relevant = False
                if ready:
                    try:
                        chunk = os.read(fd, 4096)
                    except OSError:
                        break
                    if chunk == b"":
                        break
                    pending.extend(chunk)
                    relevant = self._consume_subscribe_buffer(pending)
                elif proc.poll() is not None:
                    break
                # Sample on relevant events, and on every select timeout as a
                # latency ceiling when subscribe output is delayed/buffered.
                if relevant or not ready:
                    self._poll_once()
        finally:
            self._kill_subscribe_proc()

    def _consume_subscribe_buffer(self, pending: bytearray) -> bool:
        """Parse complete lines from *pending*; return True if any were relevant."""
        relevant = False
        while True:
            newline = pending.find(b"\n")
            if newline < 0:
                break
            raw = bytes(pending[:newline])
            del pending[: newline + 1]
            try:
                line = raw.decode("utf-8", errors="replace")
            except Exception:
                continue
            if pactl_subscribe_is_relevant(line):
                relevant = True
        return relevant

    def _poll_once(self) -> None:
        if not self._should_watch():
            return
        try:
            level = max(0.0, min(1.0, float(self._read_level())))
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            log.debug("Could not read stack volume level", exc_info=True)
            return
        with self._lock:
            last = self._last_level
            if last is not None and abs(last - level) < _DELTA:
                return
            self._last_level = level
            is_first = last is None
        if is_first:
            # Establish baseline without treating current OS level as an inbound event.
            return
        try:
            self._on_external(level)
        except Exception:
            log.debug("Inbound volume notify failed", exc_info=True)

    def _wait_interruptible(self, seconds: float) -> None:
        # Wake early on reset so endpoint switches re-baseline quickly.
        deadline = time.monotonic() + seconds
        while not self._stop.is_set():
            if self._reset.is_set():
                self._reset.clear()
                with self._lock:
                    self._last_level = None
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._stop.wait(min(0.05, remaining))

    def _kill_subscribe_proc(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is None:
            return
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                pass
