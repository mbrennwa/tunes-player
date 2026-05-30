"""Minimal Libadwaita shell — placeholder for the full player UI."""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gtk  # noqa: E402

from tunes_player.core.services import PlayerService


class TunesWindow(Adw.ApplicationWindow):
    def __init__(self, *, application: Adw.Application, service: PlayerService) -> None:
        super().__init__(application=application, title="Tunes")
        self._service = service
        self.set_default_size(960, 640)

        header = Adw.HeaderBar()
        title = Gtk.Label(label="Tunes")
        title.add_css_class("title")
        header.set_title_widget(title)

        placeholder = Gtk.Label(
            label="Local library and playback coming soon.",
            vexpand=True,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        placeholder.add_css_class("dim-label")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        box.append(placeholder)
        self.set_content(box)


def run() -> int:
    service = PlayerService()

    class TunesApplication(Adw.Application):
        def do_activate(self) -> None:  # noqa: N802 — GTK vfunc
            window = self.get_active_window()
            if window is None:
                window = TunesWindow(application=self, service=service)
            window.present()

    app = TunesApplication(application_id="io.github.mbrennwa.Tunes")
    return app.run(None)
