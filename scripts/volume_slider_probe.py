#!/usr/bin/env python3
"""Minimal GTK volume slider → wpctl, for #129 bisect.

Findings so far:
- CLI wpctl ramps: smooth
- GTK live/debounce/release: funky (jumps / wrong while dragging)
- ``none``: no audible change → glitch is from volume sets, not GTK drag itself
- ``ramp-release`` (12×200ms): smooth but laggy → Speakers wants gradual steps;
  need a faster follow

Modes (``TUNES_VOLUME_PROBE_MODE``)::

    live           — wpctl on every change-value (funky)
    debounce       — wpctl at most every 200ms
    release        — one wpctl on drag-end
    ramp-release   — slow CLI-like ramp on release (smooth, laggy)
    chase          — live target; chase with small steps every ~16ms (try this)
    none           — no wpctl (control)

Usage::

    TUNES_VOLUME_PROBE_MODE=chase .venv/bin/python scripts/volume_slider_probe.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

_DEBOUNCE_MS = 200
_RAMP_STEP_MS = 200
_RAMP_STEPS = 12
# Chase: rate-limit sink steps so Speakers DSP does not jump; stay snappy.
_CHASE_TICK_MS = 16
_CHASE_MAX_STEP = 0.04  # ~25 steps for a full 0→1 sweep (~400ms)
_MODES = frozenset(
    {"live", "debounce", "release", "ramp-release", "chase", "none"}
)


def _wpctl() -> str:
    path = shutil.which("wpctl")
    if path is None:
        sys.stderr.write("wpctl not found\n")
        sys.exit(1)
    return path


def _target() -> str:
    return os.environ.get("TUNES_VOLUME_PROBE_TARGET", "@DEFAULT_AUDIO_SINK@")


def _mode() -> str:
    raw = os.environ.get("TUNES_VOLUME_PROBE_MODE", "live").strip().casefold()
    if raw not in _MODES:
        sys.stderr.write(
            f"unknown mode {raw!r}; use {'|'.join(sorted(_MODES))}\n"
        )
        sys.exit(1)
    return raw


def get_volume(wpctl: str, target: str) -> float:
    out = subprocess.run(
        [wpctl, "get-volume", target],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    for part in out.replace(":", " ").split():
        try:
            return max(0.0, min(1.0, float(part)))
        except ValueError:
            continue
    return 0.72


def set_volume(wpctl: str, target: str, level: float) -> None:
    level = max(0.0, min(1.0, level))
    subprocess.run(
        [wpctl, "set-volume", target, f"{level:.4f}"],
        check=False,
        capture_output=True,
        text=True,
    )


class ProbeWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Volume slider probe")
        self.set_default_size(520, 150)
        self._wpctl = _wpctl()
        self._target = _target()
        self._mode = _mode()
        self._pending: float | None = None
        self._debounce_id: int | None = None
        self._ramp_id: int | None = None
        self._ramp_levels: list[float] = []
        self._chase_id: int | None = None
        self._chase_target: float | None = None
        self._device_level = get_volume(self._wpctl, self._target)

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        self.set_content(box)

        hint = {
            "none": "no wpctl — control experiment",
            "ramp-release": "slow stepped ramp on release (smooth, laggy)",
            "chase": "follow thumb with small ~16ms steps (responsive + smooth?)",
            "release": "one wpctl on release only",
            "debounce": "wpctl every 200ms max",
            "live": "wpctl on every tick",
        }[self._mode]
        self._label = Gtk.Label(
            label=f"mode={self._mode}  target={self._target}\n{hint}",
            xalign=0,
        )
        self._label.add_css_class("dim-label")
        box.append(self._label)

        self._value = Gtk.Label(label="", xalign=0)
        box.append(self._value)

        self._scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL,
            0.0,
            1.0,
            0.01,
        )
        self._scale.set_hexpand(True)
        self._scale.set_draw_value(False)
        self._scale.set_value(self._device_level)
        self._scale.connect("change-value", self._on_change_value)
        gesture = Gtk.GestureDrag.new()
        gesture.connect("drag-begin", lambda *_: self._on_drag_begin())
        gesture.connect("drag-end", lambda *_: self._on_drag_end())
        self._scale.add_controller(gesture)
        box.append(self._scale)
        self._show(self._device_level, note="initial")

    def _show(self, level: float, *, note: str) -> None:
        self._value.set_label(
            f"{note}: thumb={level:.4f}  last_wpctl={self._device_level:.4f}"
        )

    def _apply(self, level: float, *, note: str) -> None:
        if self._mode == "none":
            self._show(level, note=note)
            return
        level = max(0.0, min(1.0, level))
        set_volume(self._wpctl, self._target, level)
        self._device_level = level
        self._show(level, note=note)

    def _cancel_timers(self) -> None:
        if self._debounce_id is not None:
            GLib.source_remove(self._debounce_id)
            self._debounce_id = None
        if self._ramp_id is not None:
            GLib.source_remove(self._ramp_id)
            self._ramp_id = None
        self._ramp_levels.clear()
        if self._chase_id is not None:
            GLib.source_remove(self._chase_id)
            self._chase_id = None

    def _ensure_chase_timer(self) -> None:
        if self._chase_id is not None:
            return
        self._chase_id = GLib.timeout_add(_CHASE_TICK_MS, self._on_chase_tick)

    def _on_chase_tick(self) -> bool:
        target = self._chase_target
        if target is None:
            self._chase_id = None
            return False
        cur = self._device_level
        delta = target - cur
        if abs(delta) < 1e-4:
            self._chase_target = None
            self._chase_id = None
            self._show(cur, note="chase-caught")
            return False
        step = max(-_CHASE_MAX_STEP, min(_CHASE_MAX_STEP, delta))
        self._apply(cur + step, note="chase")
        return True

    def _on_drag_begin(self) -> None:
        if self._mode != "chase":
            self._cancel_timers()

    def _on_drag_end(self) -> None:
        level = max(0.0, min(1.0, self._scale.get_value()))
        if self._mode == "none":
            self._show(level, note="release (no wpctl)")
            return
        if self._mode == "release":
            self._apply(level, note="release")
            return
        if self._mode == "ramp-release":
            self._start_ramp(self._device_level, level)
            return
        if self._mode == "chase":
            self._chase_target = level
            self._ensure_chase_timer()
            return
        if self._mode == "debounce" and self._pending is not None:
            pending = self._pending
            self._pending = None
            self._apply(pending, note="debounce-flush")

    def _start_ramp(self, start: float, end: float) -> None:
        self._cancel_timers()
        if abs(end - start) < 1e-4:
            self._apply(end, note="ramp-noop")
            return
        self._ramp_levels = [
            start + (end - start) * i / _RAMP_STEPS
            for i in range(1, _RAMP_STEPS + 1)
        ]
        self._ramp_id = GLib.timeout_add(_RAMP_STEP_MS, self._on_ramp_tick)
        self._show(end, note="ramp-armed")

    def _on_ramp_tick(self) -> bool:
        if not self._ramp_levels:
            self._ramp_id = None
            return False
        level = self._ramp_levels.pop(0)
        self._apply(level, note="ramp")
        if not self._ramp_levels:
            self._ramp_id = None
            return False
        return True

    def _on_debounce_fire(self) -> bool:
        self._debounce_id = None
        if self._pending is None:
            return False
        level = self._pending
        self._pending = None
        self._apply(level, note="debounce")
        return False

    def _on_change_value(
        self,
        _scale: Gtk.Scale,
        _scroll_type: Gtk.ScrollType,
        value: float,
    ) -> bool:
        level = max(0.0, min(1.0, value))
        if self._mode in {"none", "release", "ramp-release"}:
            self._show(level, note="drag")
            return False
        if self._mode == "chase":
            self._chase_target = level
            self._show(level, note="chase-target")
            self._ensure_chase_timer()
            return False
        if self._mode == "live":
            self._apply(level, note="live")
            return False
        # debounce
        self._pending = level
        self._show(level, note="pending")
        if self._debounce_id is not None:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(_DEBOUNCE_MS, self._on_debounce_fire)
        return False


class ProbeApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="tunes.player.volume-probe")
        self.connect("activate", self._on_activate)

    def _on_activate(self, _app: Adw.Application) -> None:
        win = ProbeWindow(self)
        win.present()


def main() -> None:
    app = ProbeApp()
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
