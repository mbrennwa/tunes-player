"""Review labelled releases that could not be loaded into the grid."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gtk  # noqa: E402

from tunes_player.core.services import PlayerService


class UnavailableLabelledDialog(Adw.Dialog):
    """List tagged releases that Labelled skipped, with remove-labels actions."""

    def __init__(
        self,
        *,
        service: PlayerService,
        release_ids: Sequence[str],
        on_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._on_changed = on_changed
        self._release_ids = tuple(release_ids)

        self.set_title("Unavailable labelled releases")
        self.set_content_width(480)
        self.set_content_height(420)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)
        self.set_child(toolbar)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(8)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        intro = Gtk.Label(
            label=(
                "These releases still have labels, but could not be loaded "
                "(removed from the catalog, offline, or missing locally)."
            ),
            wrap=True,
            xalign=0,
        )
        intro.add_css_class("dim-label")
        box.append(intro)

        scrolled = Gtk.ScrolledWindow(
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        self._list = Gtk.ListBox()
        self._list.add_css_class("boxed-list")
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled.set_child(self._list)
        box.append(scrolled)

        remove_all = Gtk.Button(label="Remove labels from all")
        remove_all.add_css_class("destructive-action")
        remove_all.set_halign(Gtk.Align.START)
        remove_all.connect("clicked", self._on_remove_all)
        box.append(remove_all)

        toolbar.set_content(box)
        self._rebuild()

    def _rebuild(self) -> None:
        child = self._list.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._list.remove(child)
            child = next_child

        if not self._release_ids:
            row = Adw.ActionRow(title="No unavailable labelled releases")
            row.set_sensitive(False)
            self._list.append(row)
            return

        for release_id in self._release_ids:
            labels = sorted(
                self._service.get_release_labels(release_id),
                key=lambda item: item.casefold(),
            )
            subtitle = ", ".join(labels) if labels else "(no labels)"
            row = Adw.ActionRow(title=release_id, subtitle=subtitle)
            btn = Gtk.Button(label="Remove labels")
            btn.set_valign(Gtk.Align.CENTER)
            btn.add_css_class("flat")
            btn.connect(
                "clicked",
                lambda _b, rid=release_id: self._remove_labels(rid),
            )
            row.add_suffix(btn)
            self._list.append(row)

    def _remove_labels(self, release_id: str) -> None:
        self._service.set_release_labels(release_id, frozenset())
        self._release_ids = tuple(rid for rid in self._release_ids if rid != release_id)
        self._rebuild()
        if self._on_changed is not None:
            self._on_changed()
        if not self._release_ids:
            self.close()

    def _on_remove_all(self, *_args: object) -> None:
        for release_id in list(self._release_ids):
            self._service.set_release_labels(release_id, frozenset())
        self._release_ids = ()
        self._rebuild()
        if self._on_changed is not None:
            self._on_changed()
        self.close()
