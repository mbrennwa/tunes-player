"""Right-click release label editor popover."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gtk  # noqa: E402

from tunes_player.core.services import PlayerService

_LIST_WIDTH = 260


class ReleaseLabelEditor(Gtk.Popover):
    def __init__(
        self,
        *,
        service: PlayerService,
        release_id: str,
        on_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._release_id = release_id
        self._on_changed = on_changed
        self._updating = False
        self._checks: dict[str, Gtk.CheckButton] = {}

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_size_request(_LIST_WIDTH, -1)
        self.set_child(box)

        heading = Gtk.Label(label="Labels")
        heading.add_css_class("heading")
        heading.set_halign(Gtk.Align.START)
        box.append(heading)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        box.append(self._list)

        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._entry = Gtk.Entry()
        self._entry.set_placeholder_text("New label…")
        self._entry.set_hexpand(True)
        self._entry.connect("activate", self._on_add_clicked)
        add_row.append(self._entry)

        add_btn = Gtk.Button(label="Add")
        add_btn.connect("clicked", self._on_add_clicked)
        add_row.append(add_btn)
        box.append(add_row)

        self._rebuild_checks()

    def _rebuild_checks(self) -> None:
        child = self._list.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._list.remove(child)
            child = next_child
        self._checks.clear()

        all_labels = self._service.list_user_labels()
        current = self._service.get_release_labels(self._release_id)
        self._updating = True
        try:
            for label in all_labels:
                check = Gtk.CheckButton(label=label)
                check.set_active(label in current)
                check.connect("toggled", self._on_check_toggled, label)
                row = Gtk.ListBoxRow()
                row.set_child(check)
                self._list.append(row)
                self._checks[label] = check
        finally:
            self._updating = False

    def _notify_changed(self) -> None:
        if self._on_changed is not None:
            self._on_changed()

    def _on_check_toggled(self, check: Gtk.CheckButton, label: str) -> None:
        if self._updating:
            return
        self._service.toggle_release_label(
            self._release_id,
            label,
            on=check.get_active(),
        )
        self._notify_changed()

    def _on_add_clicked(self, *_args: object) -> None:
        text = self._entry.get_text().strip()
        if not text:
            return
        self._service.toggle_release_label(self._release_id, text, on=True)
        self._entry.set_text("")
        self._rebuild_checks()
        self._notify_changed()


def _picked_has_css_class(widget: Gtk.Widget, x: float, y: float, css_class: str) -> bool:
    picked = widget.pick(x, y, Gtk.PickFlags.DEFAULT)
    while picked is not None:
        if picked.has_css_class(css_class):
            return True
        picked = picked.get_parent()
    return False


def attach_release_label_menu(
    tile: Gtk.Widget,
    *,
    service: PlayerService,
    release_id: str,
    on_changed: Callable[[], None] | None = None,
) -> None:
    popover: ReleaseLabelEditor | None = None

    def show_popover() -> None:
        nonlocal popover
        if popover is None:
            popover = ReleaseLabelEditor(
                service=service,
                release_id=release_id,
                on_changed=on_changed,
            )
            popover.set_parent(tile)
        else:
            popover._release_id = release_id
            popover._rebuild_checks()
        popover.popup()

    gesture = Gtk.GestureClick()
    gesture.set_button(Gdk.BUTTON_SECONDARY)

    def _on_pressed(_gesture: Gtk.GestureClick, _n_press: int, x: float, y: float) -> None:
        if _picked_has_css_class(tile, x, y, "release-art-play"):
            return
        if _picked_has_css_class(tile, x, y, "artist-link"):
            return
        show_popover()

    gesture.connect("pressed", _on_pressed)
    tile.add_controller(gesture)
