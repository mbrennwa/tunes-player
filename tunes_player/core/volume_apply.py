"""Coalesce outbound device-volume applies and gate inbound stack echoes."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from tunes_player.core.volume import VolumeController

log = logging.getLogger(__name__)

MainThreadHook = Callable[[Callable[[], None]], None]
InboundLevelHandler = Callable[[float], None]


class VolumeApplyCoordinator:
    """Own pending/suppress/gesture around :class:`VolumeController` writes.

    Level state (``_volume`` / ``_muted``) and engine soft-gain stay on the
    PlayerService façade; this collaborator only serializes device applies and
    filters inbound subscribe echoes.
    """

    def __init__(
        self,
        *,
        get_controller: Callable[[], VolumeController | None],
        run_on_main_thread: MainThreadHook,
        apply_inbound_level: InboundLevelHandler,
    ) -> None:
        self._get_controller = get_controller
        self._run_on_main_thread = run_on_main_thread
        self._apply_inbound_level = apply_inbound_level
        self._lock = threading.Lock()
        self._pending: float | None = None
        self._inflight = False
        self._suppress_inbound_depth = 0
        self._gesture_active = False

    def begin_gesture(self) -> None:
        """Ignore inbound stack volume while the UI slider is being dragged."""
        self._gesture_active = True

    def end_gesture(self) -> None:
        self._gesture_active = False

    def on_device_volume_level(self, level: float) -> None:
        """Inbound device/stack volume from ``VolumeController.subscribe()``."""
        if self._suppress_inbound_depth > 0:
            return
        if self._gesture_active:
            return
        clamped = max(0.0, min(1.0, level))

        def apply() -> None:
            if self._suppress_inbound_depth > 0:
                return
            if self._gesture_active:
                return
            self._apply_inbound_level(clamped)

        self._run_on_main_thread(apply)

    def schedule_apply(self, level: float) -> None:
        """Coalesce sink/mixer applies off the caller thread (latest wins)."""
        with self._lock:
            self._pending = max(0.0, min(1.0, level))
            if self._inflight:
                return
            self._inflight = True
        threading.Thread(
            target=self._apply_worker,
            name="volume-apply",
            daemon=True,
        ).start()

    def set_level_sync(self, level: float) -> None:
        """Blocking device-volume write (mode transitions); echo-suppressed."""
        controller = self._get_controller()
        if controller is None:
            return
        with self._lock:
            self._pending = None
        self._suppress_inbound_depth += 1
        try:
            controller.set_level(max(0.0, min(1.0, level)))
        except OSError:
            log.debug("Could not set device volume", exc_info=True)
        finally:
            self._suppress_inbound_depth = max(0, self._suppress_inbound_depth - 1)

    def flush(self, *, timeout: float = 2.0) -> None:
        """Block until coalesced device-volume applies finish (tests / shutdown)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self._inflight and self._pending is None:
                    return
            time.sleep(0.005)
        raise TimeoutError("device volume apply did not drain")

    def prepare_shutdown(self) -> None:
        """Drop gesture + pending work before a bounded flush on quit."""
        self._gesture_active = False
        with self._lock:
            self._pending = None

    def _apply_worker(self) -> None:
        while True:
            with self._lock:
                pending = self._pending
                if pending is None:
                    self._inflight = False
                    return
                self._pending = None
            controller = self._get_controller()
            if controller is None:
                continue
            self._suppress_inbound_depth += 1
            try:
                controller.set_level(pending)
            except OSError:
                log.debug("Device volume apply failed", exc_info=True)
            finally:
                self._suppress_inbound_depth = max(
                    0, self._suppress_inbound_depth - 1
                )
