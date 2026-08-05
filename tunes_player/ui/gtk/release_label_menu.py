"""Release label editor presented as an Adw.Dialog (not a nested popover)."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gtk  # noqa: E402

from tunes_player.core.services import PlayerService

_LIST_WIDTH = 280


class ReleaseLabelEditor(Adw.Dialog):
    """Edit labels for one release.

    Uses a dialog instead of a popover so it is not auto-hidden by the
    right-click action menu that opens it (GTK4 popover-on-popover grab).
    """

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
        # Defer flags_changed until close so Add/toggles stay snappy.
        self._pending_side_effects = False
        self._writes_in_flight = 0
        self._closed = False

        self.set_title("Labels")
        self.set_content_width(_LIST_WIDTH + 48)
        self.set_content_height(360)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)
        self.set_child(toolbar)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_size_request(_LIST_WIDTH, -1)

        scrolled = Gtk.ScrolledWindow(
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list.add_css_class("boxed-list")
        scrolled.set_child(self._list)
        box.append(scrolled)

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

        toolbar.set_content(box)

        self.connect("closed", self._on_closed)
        self._rebuild_checks()

    def present_for(self, parent: Gtk.Widget | None = None) -> None:
        """Show (or re-show) the editor for the current release id."""
        self._closed = False
        self._rebuild_checks()
        self.present(parent)

    def set_release_id(self, release_id: str) -> None:
        self._release_id = release_id

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
                self._append_check(label, active=label in current)
        finally:
            self._updating = False

    def _append_check(self, label: str, *, active: bool) -> None:
        check = Gtk.CheckButton(label=label)
        check.set_active(active)
        check.connect("toggled", self._on_check_toggled, label)
        row = Gtk.ListBoxRow()
        row.set_child(check)
        self._list.append(row)
        self._checks[label] = check

    def _notify_changed(self) -> None:
        if self._on_changed is not None:
            self._on_changed()

    def _flush_side_effects(self) -> None:
        if not self._pending_side_effects:
            return
        if self._writes_in_flight > 0:
            return
        self._pending_side_effects = False
        self._service.notify_flags_changed()
        self._service.schedule_labels_sync()

    def _persist_toggle(self, label: str, *, on: bool) -> None:
        self._pending_side_effects = True
        self._writes_in_flight += 1

        def _on_done() -> None:
            self._writes_in_flight = max(0, self._writes_in_flight - 1)
            if self._closed:
                self._flush_side_effects()

        self._service.toggle_release_label_async(
            self._release_id,
            label,
            on=on,
            emit_changed=False,
            on_done=_on_done,
        )
        self._notify_changed()

    def _on_closed(self, *_args: object) -> None:
        self._closed = True
        self._flush_side_effects()

    def _on_check_toggled(self, check: Gtk.CheckButton, label: str) -> None:
        if self._updating:
            return
        self._persist_toggle(label, on=check.get_active())

    def _on_add_clicked(self, *_args: object) -> None:
        text = self._entry.get_text().strip()
        if not text:
            return
        self._entry.set_text("")
        # Optimistic UI — DB write is async; notify waits until dialog close.
        existing = self._checks.get(text)
        self._updating = True
        try:
            if existing is None:
                self._append_check(text, active=True)
            else:
                existing.set_active(True)
        finally:
            self._updating = False
        self._persist_toggle(text, on=True)
