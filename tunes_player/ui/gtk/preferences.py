"""Application preferences — placeholders until settings backend lands."""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw  # noqa: E402


class PreferencesWindow(Adw.PreferencesWindow):
    def __init__(self, *, parent: Adw.ApplicationWindow) -> None:
        super().__init__()
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_title("Settings")

        library = Adw.PreferencesGroup(title="Library")
        library.add(
            Adw.ActionRow(
                title="Music folders",
                subtitle="Local library scanning coming soon",
            )
        )

        library_page = Adw.PreferencesPage(title="Library", icon_name="folder-music-symbolic")
        library_page.add(library)

        audio = Adw.PreferencesGroup(title="Audio")
        audio.add(
            Adw.SwitchRow(
                title="Bit-perfect playback",
                subtitle="No in-app resampling or soft gain when enabled",
                active=True,
            )
        )
        audio.add(
            Adw.ActionRow(
                title="Output device",
                subtitle="Endpoint volume via PipeWire / ALSA (planned)",
            )
        )

        audio_page = Adw.PreferencesPage(title="Audio", icon_name="audio-speakers-symbolic")
        audio_page.add(audio)

        self.add(library_page)
        self.add(audio_page)
