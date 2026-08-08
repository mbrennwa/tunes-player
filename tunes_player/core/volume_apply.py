"""Chase outbound device-volume applies and gate inbound stack echoes."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from tunes_player.core.volume import VolumeController, debug_volume_trace

log = logging.getLogger(__name__)

MainThreadHook = Callable[[Callable[[], None]], None]
InboundLevelHandler = Callable[[float], None]

# Match scripts/volume_slider_probe.py chase mode (#129): Speakers/DSP sinks
# dislike abrupt wpctl jumps; small steps toward the UI target sound smooth.
_CHASE_TICK_SEC = 0.016
_CHASE_MAX_STEP = 0.04
_CHASE_EPS = 1e-4


class VolumeApplyCoordinator:
    """Own target/chase/suppress/gesture around :class:`VolumeController` writes.

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
        self._target: float | None = None
        self._applied: float | None = None
        self._inflight = False
        self._suppress_inbound_depth = 0
        self._gesture_active = False

    def begin_gesture(self) -> None:
        """Ignore inbound stack volume while the UI slider is being dragged."""
        self._gesture_active = True

    def end_gesture(self) -> None:
        """End inbound suppress; chase continues toward the latest target."""
        self._gesture_active = False
        self._kick_chase_worker()

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
        """Chase sink/mixer level toward ``level`` off the caller thread."""
        with self._lock:
            self._target = max(0.0, min(1.0, level))
            debug_volume_trace(
                "schedule_device target=%.4f applied=%s inflight=%s gesture=%s",
                self._target,
                self._applied,
                self._inflight,
                self._gesture_active,
            )
            if self._inflight:
                return
            self._inflight = True
        threading.Thread(
            target=self._chase_worker,
            name="volume-apply",
            daemon=True,
        ).start()

    def _kick_chase_worker(self) -> None:
        """Start the chase worker if a target is waiting."""
        with self._lock:
            if self._target is None or self._inflight:
                return
            if (
                self._applied is not None
                and abs(self._target - self._applied) < _CHASE_EPS
            ):
                self._target = None
                return
            self._inflight = True
        threading.Thread(
            target=self._chase_worker,
            name="volume-apply",
            daemon=True,
        ).start()

    def set_level_sync(self, level: float) -> None:
        """Blocking device-volume write (mode transitions); hard snap, no chase."""
        controller = self._get_controller()
        if controller is None:
            return
        clamped = max(0.0, min(1.0, level))
        with self._lock:
            self._target = None
            self._applied = clamped
        self._suppress_inbound_depth += 1
        try:
            controller.set_level(clamped)
        except OSError:
            log.debug("Could not set device volume", exc_info=True)
        finally:
            self._suppress_inbound_depth = max(0, self._suppress_inbound_depth - 1)

    def flush(self, *, timeout: float = 2.0) -> None:
        """Block until chase catches the target (tests / shutdown)."""
        self._kick_chase_worker()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self._inflight and self._target is None:
                    return
            time.sleep(0.005)
        raise TimeoutError("device volume apply did not drain")

    def prepare_shutdown(self) -> None:
        """Drop gesture + pending chase before a bounded flush on quit."""
        self._gesture_active = False
        with self._lock:
            self._target = None

    def _seed_applied(self, controller: VolumeController) -> float:
        """Return the current applied level, seeding from the device if needed."""
        with self._lock:
            if self._applied is not None:
                return self._applied
        try:
            seeded = max(0.0, min(1.0, controller.get_level()))
        except OSError:
            with self._lock:
                seeded = self._target if self._target is not None else 0.0
        with self._lock:
            if self._applied is None:
                self._applied = seeded
            return self._applied

    def _chase_worker(self) -> None:
        while True:
            with self._lock:
                target = self._target
                applied = self._applied
                if target is None:
                    self._inflight = False
                    return
                if applied is not None and abs(target - applied) < _CHASE_EPS:
                    self._target = None
                    self._inflight = False
                    return
            controller = self._get_controller()
            if controller is None:
                with self._lock:
                    self._target = None
                    self._inflight = False
                return
            applied = self._seed_applied(controller)
            with self._lock:
                target = self._target
                if target is None:
                    self._inflight = False
                    return
                if abs(target - applied) < _CHASE_EPS:
                    self._target = None
                    self._applied = target
                    self._inflight = False
                    return
                delta = target - applied
                step = max(-_CHASE_MAX_STEP, min(_CHASE_MAX_STEP, delta))
                next_level = max(0.0, min(1.0, applied + step))
            self._suppress_inbound_depth += 1
            try:
                debug_volume_trace(
                    "device chase set_level=%.4f target=%.4f (wpctl/amixer)",
                    next_level,
                    target,
                )
                controller.set_level(next_level)
            except OSError:
                log.debug("Device volume apply failed", exc_info=True)
                with self._lock:
                    self._inflight = False
                return
            finally:
                self._suppress_inbound_depth = max(
                    0, self._suppress_inbound_depth - 1
                )
            with self._lock:
                self._applied = next_level
                if (
                    self._target is not None
                    and abs(self._target - next_level) < _CHASE_EPS
                ):
                    self._target = None
                    self._inflight = False
                    return
            time.sleep(_CHASE_TICK_SEC)
