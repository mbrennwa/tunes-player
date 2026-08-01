"""Header Downloads button and popover (Firefox-style Save-to-disk list)."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")

from gi.repository import GLib, Gtk, Pango  # noqa: E402

from tunes_player.core.save_to_disk import DownloadJobInfo
from tunes_player.core.services import PlayerService
from tunes_player.ui.gtk.util import open_external_uri

_POPOVER_WIDTH = 320
_DOWNLOAD_EVENTS = frozenset(
    {
        "download_started",
        "download_resumed",
        "download_progress",
        "download_finished",
        "download_cancelled",
        "download_cancelling",
        "download_error",
        "download_queued",
    }
)


class DownloadsMenu:
    """Header control: button + popover listing active/queued/completed downloads."""

    def __init__(self, service: PlayerService) -> None:
        self._service = service
        self._rebuild_pending = False

        self._button = Gtk.Button(icon_name="folder-download-symbolic")
        self._button.set_tooltip_text("Downloads")
        self._button.add_css_class("flat")
        self._button.connect("clicked", self._on_button_clicked)

        self._box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._box.set_margin_top(8)
        self._box.set_margin_bottom(8)
        self._box.set_margin_start(10)
        self._box.set_margin_end(10)
        self._box.set_size_request(_POPOVER_WIDTH, -1)

        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scrolled.set_max_content_height(360)
        self._scrolled.set_propagate_natural_height(True)

        self._list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._scrolled.set_child(self._list_box)
        self._box.append(self._scrolled)

        self._footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        open_btn = Gtk.Button(label="Open Downloads folder")
        open_btn.add_css_class("flat")
        open_btn.set_halign(Gtk.Align.START)
        open_btn.connect("clicked", self._on_open_downloads_folder)
        self._footer.append(open_btn)
        self._box.append(self._footer)

        self._popover = Gtk.Popover()
        self._popover.set_child(self._box)
        self._popover.set_parent(self._button)

        service.subscribe(lambda event: GLib.idle_add(self._on_event, event))
        self._rebuild()

    @property
    def button(self) -> Gtk.Button:
        return self._button

    def _on_event(self, event: str) -> bool:
        if event in _DOWNLOAD_EVENTS:
            self._schedule_rebuild()
        return False

    def _schedule_rebuild(self) -> None:
        if self._rebuild_pending:
            return
        self._rebuild_pending = True
        GLib.idle_add(self._rebuild_idle)

    def _rebuild_idle(self) -> bool:
        self._rebuild_pending = False
        self._rebuild()
        return False

    def _on_button_clicked(self, *_args: object) -> None:
        self._rebuild()
        self._popover.popup()

    def _rebuild(self) -> None:
        while (child := self._list_box.get_first_child()) is not None:
            self._list_box.remove(child)

        snapshot = self._service.download_jobs()
        busy = self._service.has_download_activity()
        if busy:
            self._button.add_css_class("suggested-action")
        else:
            self._button.remove_css_class("suggested-action")

        if (
            snapshot.active is None
            and not snapshot.pending
            and not snapshot.completed
        ):
            empty = Gtk.Label(label="No downloads")
            empty.add_css_class("dim-label")
            empty.set_halign(Gtk.Align.START)
            self._list_box.append(empty)
            return

        if snapshot.active is not None:
            self._list_box.append(self._section_label("Ongoing"))
            self._list_box.append(self._active_row(snapshot.active))

        if snapshot.pending:
            self._list_box.append(self._section_label("Upcoming"))
            for job in snapshot.pending:
                self._list_box.append(self._pending_row(job))

        if snapshot.completed:
            self._list_box.append(self._section_label("Completed"))
            for job in snapshot.completed:
                self._list_box.append(self._completed_row(job))

    def _section_label(self, text: str) -> Gtk.Label:
        label = Gtk.Label(label=text)
        label.add_css_class("heading")
        label.set_halign(Gtk.Align.START)
        return label

    def _active_row(self, job: DownloadJobInfo) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        title = Gtk.Label(label=job.label)
        title.set_halign(Gtk.Align.START)
        title.set_xalign(0.0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_wrap(False)
        text.append(title)
        if job.progress is not None:
            current, total, track_title = job.progress
            sub = Gtk.Label(label=f"{current}/{total}: {track_title}")
            sub.add_css_class("dim-label")
            sub.set_halign(Gtk.Align.START)
            sub.set_xalign(0.0)
            sub.set_ellipsize(Pango.EllipsizeMode.END)
            text.append(sub)
        else:
            sub = Gtk.Label(label=f"{job.track_count} track(s)")
            sub.add_css_class("dim-label")
            sub.set_halign(Gtk.Align.START)
            text.append(sub)
        row.append(text)
        cancel = Gtk.Button(label="Cancel")
        cancel.add_css_class("flat")
        cancel.connect(
            "clicked",
            lambda *_: self._service.cancel_save_to_disk(job.job_id),
        )
        row.append(cancel)
        return row

    def _pending_row(self, job: DownloadJobInfo) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        title = Gtk.Label(label=job.label)
        title.set_halign(Gtk.Align.START)
        title.set_xalign(0.0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        text.append(title)
        sub = Gtk.Label(label=f"{job.track_count} track(s) · queued")
        sub.add_css_class("dim-label")
        sub.set_halign(Gtk.Align.START)
        text.append(sub)
        row.append(text)
        remove = Gtk.Button(label="Remove")
        remove.add_css_class("flat")
        remove.connect(
            "clicked",
            lambda *_: self._service.cancel_save_to_disk(job.job_id),
        )
        row.append(remove)
        return row

    def _completed_row(self, job: DownloadJobInfo) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        title = Gtk.Label(label=job.label)
        title.set_halign(Gtk.Align.START)
        title.set_xalign(0.0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        text.append(title)
        if job.status == "failed":
            detail = job.error or "Failed"
        else:
            detail = f"{job.track_count} track(s) saved"
        sub = Gtk.Label(label=detail)
        sub.add_css_class("dim-label")
        sub.set_halign(Gtk.Align.START)
        sub.set_xalign(0.0)
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        text.append(sub)
        row.append(text)
        open_btn = Gtk.Button(label="Open folder")
        open_btn.add_css_class("flat")
        open_btn.connect(
            "clicked",
            lambda *_: self._open_folder(job.dest_dir),
        )
        row.append(open_btn)
        return row

    def _on_open_downloads_folder(self, *_args: object) -> None:
        raw = self._service.config.config.download_folder
        if raw:
            self._open_folder(raw)

    def _open_folder(self, folder: str) -> None:
        try:
            path = Path(folder).expanduser().resolve()
        except OSError:
            return
        # Prefer the album folder; if missing (failed job), walk up to an existing dir.
        while True:
            try:
                if path.is_dir():
                    open_external_uri(path.as_uri())
                    return
            except OSError:
                return
            if path.parent == path:
                return
            path = path.parent


def attach_downloads_menu(header: object, service: PlayerService) -> DownloadsMenu:
    """Pack a Downloads button at the end of an Adw.HeaderBar (left of later pack_end)."""
    menu = DownloadsMenu(service)
    pack_end = getattr(header, "pack_end", None)
    if callable(pack_end):
        pack_end(menu.button)
    return menu
