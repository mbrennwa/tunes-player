"""Application preferences."""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from tunes_player.core.library import ScanResult
from tunes_player.core.services import PlayerService
from tunes_player.ui.gtk.util import escape_markup, open_external_uri


class PreferencesWindow(Adw.PreferencesWindow):
    def __init__(self, *, parent: Adw.ApplicationWindow, service: PlayerService) -> None:
        super().__init__()
        self._service = service
        self._parent = parent
        self._dynamic_rows: list[Adw.ActionRow] = []
        self._scan_poll_id: int = 0
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_title("Settings")

        self._folders_group = Adw.PreferencesGroup(title="Music folders")

        self._add_row = Adw.ActionRow(title="Add folder…")
        add_button = Gtk.Button(icon_name="list-add-symbolic")
        add_button.set_valign(Gtk.Align.CENTER)
        add_button.connect("clicked", self._on_add_folder_clicked)
        self._add_row.add_suffix(add_button)
        self._add_row.set_activatable_widget(add_button)
        self._folders_group.add(self._add_row)

        self._scan_row = Adw.ActionRow(
            title="Scan library",
            subtitle="Index audio files from the folders above",
        )
        scan_button = Gtk.Button(label="Scan now")
        scan_button.set_valign(Gtk.Align.CENTER)
        scan_button.connect("clicked", self._on_scan_clicked)
        self._scan_row.add_suffix(scan_button)
        self._scan_row.set_activatable_widget(scan_button)
        self._scan_button = scan_button

        log_path = service.config.data_dir / "tunes-player.log"
        self._log_row = Adw.ActionRow(title="Log file", subtitle=str(log_path))
        copy_log_btn = Gtk.Button(label="Copy path")
        copy_log_btn.set_valign(Gtk.Align.CENTER)
        copy_log_btn.connect("clicked", lambda *_: self._copy_log_path(log_path))
        self._log_row.add_suffix(copy_log_btn)
        self._log_row.set_activatable_widget(copy_log_btn)
        diagnostics_group = Adw.PreferencesGroup(
            title="Diagnostics",
            description="Errors and warnings are appended to this file while Tunes runs.",
        )
        diagnostics_group.add(self._log_row)

        sources_page = Adw.PreferencesPage(title="Sources", icon_name="cloud-download-symbolic")
        local_group = Adw.PreferencesGroup(
            title="Local files",
            description="Folders are scanned into the local library index.",
        )
        local_group.add(self._folders_group)
        sources_page.add(local_group)

        audio = Adw.PreferencesGroup(title="Audio")
        self._bit_perfect_row = Adw.SwitchRow(
            title="Bit-perfect playback",
            subtitle=self._volume_mode_subtitle(),
            active=service.config.config.bit_perfect,
        )
        self._bit_perfect_row.connect("notify::active", self._on_bit_perfect_changed)
        audio.add(self._bit_perfect_row)

        self._output_row = Adw.ActionRow(
            title="Output device",
            subtitle="System default audio sink",
        )
        self._output_dropdown = Gtk.DropDown(model=Gtk.StringList.new([]))
        self._output_dropdown.set_valign(Gtk.Align.CENTER)
        self._output_dropdown.connect("notify::selected", self._on_output_changed)
        self._output_row.add_suffix(self._output_dropdown)
        self._output_row.set_activatable_widget(self._output_dropdown)
        audio.add(self._output_row)
        self._reload_output_sinks()

        audio_page = Adw.PreferencesPage(title="Audio", icon_name="audio-speakers-symbolic")
        audio_page.add(audio)

        self._tidal_status_row = Adw.ActionRow(title="TIDAL")
        self._tidal_sign_in_btn = Gtk.Button(label="Sign in")
        self._tidal_sign_in_btn.connect("clicked", self._on_tidal_sign_in_clicked)
        self._tidal_sign_out_btn = Gtk.Button(label="Sign out")
        self._tidal_sign_out_btn.connect("clicked", self._on_tidal_sign_out_clicked)
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_box.append(self._tidal_sign_in_btn)
        btn_box.append(self._tidal_sign_out_btn)
        btn_box.set_valign(Gtk.Align.CENTER)
        self._tidal_status_row.add_suffix(btn_box)
        tidal_group = Adw.PreferencesGroup(
            title="Streaming",
            description=(
                "Requires your own TIDAL subscription. Sign in with your browser "
                "for full-length playback."
            ),
        )
        tidal_group.add(self._tidal_status_row)
        sources_page.add(tidal_group)

        diagnostics_page = Adw.PreferencesPage(
            title="Diagnostics",
            icon_name="utilities-terminal-symbolic",
        )
        diagnostics_page.add(diagnostics_group)

        self.add(sources_page)
        self.add(audio_page)
        self.add(diagnostics_page)

        self._tidal_oauth_poll_id = 0
        self._tidal_sign_in_dialog: Adw.AlertDialog | None = None
        self._tidal_sign_in_dialog_presented = False
        self._reload_folders()
        self._reload_tidal_status()
        service.subscribe(lambda event: GLib.idle_add(self._on_service_event, event))

    def _reload_folders(self) -> None:
        for row in self._dynamic_rows:
            self._folders_group.remove(row)
        self._dynamic_rows.clear()

        if self._scan_row.get_parent() is not None:
            self._folders_group.remove(self._scan_row)

        folders = self._service.config.config.music_folders
        if not folders:
            empty = Adw.ActionRow(
                title="No folders configured",
                subtitle="Add a folder containing your music files",
            )
            empty.set_sensitive(False)
            self._folders_group.add(empty)
            self._dynamic_rows.append(empty)
        else:
            for folder in folders:
                row = Adw.ActionRow(
                    title=escape_markup(folder),
                    subtitle="Local music library",
                )
                remove_button = Gtk.Button(icon_name="user-trash-symbolic")
                remove_button.set_valign(Gtk.Align.CENTER)
                remove_button.connect("clicked", lambda _btn, path=folder: self._remove_folder(path))
                row.add_suffix(remove_button)
                self._folders_group.add(row)
                self._dynamic_rows.append(row)

        self._folders_group.add(self._scan_row)

    def _on_add_folder_clicked(self, *_args: object) -> None:
        dialog = Gtk.FileDialog(title="Choose Music Folder")
        dialog.select_folder(self._parent, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        path = folder.get_path()
        if path is None:
            return
        self._service.config.add_music_folder(path)
        self._reload_folders()

    def _remove_folder(self, folder: str) -> None:
        self._service.config.remove_music_folder(folder)
        self._reload_folders()

    def _on_bit_perfect_changed(self, row: Adw.SwitchRow, *_args: object) -> None:
        self._service.set_bit_perfect(row.get_active())
        row.set_subtitle(self._volume_mode_subtitle())

    def _volume_mode_subtitle(self) -> str:
        state = self._service.get_playback_state()
        setting_on = self._service.config.config.bit_perfect
        if state.bit_perfect:
            return "Bit-perfect uses device volume only (no mpv soft gain)"
        if setting_on and state.device_volume:
            return "Bit-perfect enabled — volume adjusts the selected audio sink"
        if setting_on:
            return (
                "Bit-perfect needs device volume; using mpv float software gain "
                "(not bit-perfect)"
            )
        if state.device_volume:
            return "Volume adjusts the selected audio sink"
        return "Device volume unavailable — using mpv float software volume"

    def _reload_output_sinks(self) -> None:
        endpoints = self._service.list_output_sinks()
        if not endpoints:
            self._output_row.set_sensitive(False)
            self._output_row.set_subtitle("No controllable sinks found")
            return

        self._output_row.set_sensitive(True)
        names = [endpoint.description for endpoint in endpoints]
        model = Gtk.StringList.new(names)
        self._output_dropdown.set_model(model)

        active_id = self._service.config.config.output_sink_id
        selected = 0
        for index, endpoint in enumerate(endpoints):
            if endpoint.id == active_id or (active_id is None and endpoint.is_default):
                selected = index
                break
        self._output_dropdown.handler_block_by_func(self._on_output_changed)
        self._output_dropdown.set_selected(selected)
        self._output_dropdown.handler_unblock_by_func(self._on_output_changed)
        self._output_row.set_subtitle(endpoints[selected].description)

    def _on_output_changed(self, dropdown: Gtk.DropDown, *_args: object) -> None:
        endpoints = self._service.list_output_sinks()
        index = dropdown.get_selected()
        if index >= len(endpoints):
            return
        endpoint = endpoints[index]
        self._service.set_output_sink(endpoint.id)
        self._output_row.set_subtitle(endpoint.description)
        self._bit_perfect_row.set_subtitle(self._volume_mode_subtitle())

    def _on_scan_clicked(self, *_args: object) -> None:
        if self._service.is_scanning():
            return
        if not self._service.config.config.music_folders:
            self._scan_row.set_subtitle("Add at least one music folder first")
            return

        self._scan_row.set_subtitle("Starting scan…")
        self._scan_button.set_sensitive(False)

        self._service.scan_library(
            on_progress=self._update_scan_progress,
            on_finished=self._update_scan_finished,
            on_error=self._update_scan_error,
        )
        if self._scan_poll_id == 0:
            self._scan_poll_id = GLib.timeout_add(200, self._poll_scan)

    def _poll_scan(self) -> bool:
        if self._service.poll_scan():
            return True
        self._scan_poll_id = 0
        return False

    def _update_scan_progress(self, current: int, total: int, path: str) -> None:
        if total == 0:
            self._scan_row.set_subtitle(path or "Discovering files…")
        else:
            name = path.rsplit("/", 1)[-1]
            self._scan_row.set_subtitle(f"Scanning {current}/{total}: {name}")

    def _update_scan_finished(self, result: ScanResult) -> None:
        self._scan_button.set_sensitive(True)
        parts = [
            f"indexed {result.indexed}",
            f"skipped {result.skipped}",
            f"removed {result.removed}",
            f"errors {result.errors}",
        ]
        if result.art_indexed:
            parts.insert(1, f"art {result.art_indexed}")
        self._scan_row.set_subtitle(f"Done — {', '.join(parts)}")
        GLib.idle_add(self._defer_library_notify)

    def _defer_library_notify(self) -> bool:
        self._service.notify_library_updated()
        return False

    def _update_scan_error(self, exc: Exception) -> None:
        self._scan_button.set_sensitive(True)
        self._scan_row.set_subtitle(f"Scan failed: {exc}")

    def _copy_text(self, text: str) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        display.get_clipboard().set(Gdk.ContentProvider.new_for_string(text))

    def _copy_log_path(self, log_path: object) -> None:
        self._copy_text(str(log_path))

    def _on_service_event(self, event: str) -> bool:
        if event == "sources_changed":
            self._reload_tidal_status()
        return False

    def _reload_tidal_status(self) -> None:
        service = self._service
        if not service.tidal_available():
            self._tidal_status_row.set_subtitle(
                "tidalapi is not installed (pip install tidalapi)"
            )
            self._tidal_sign_in_btn.set_sensitive(False)
            self._tidal_sign_out_btn.set_sensitive(False)
            return

        if service.tidal_is_logged_in():
            label = service.tidal_account_label()
            self._tidal_status_row.set_subtitle(
                f"Connected as {label}" if label else "Connected"
            )
            self._tidal_sign_in_btn.set_sensitive(False)
            self._tidal_sign_out_btn.set_sensitive(True)
        else:
            self._tidal_status_row.set_subtitle("Not connected")
            self._tidal_sign_in_btn.set_sensitive(True)
            self._tidal_sign_out_btn.set_sensitive(False)

    def _on_tidal_sign_in_clicked(self, *_args: object) -> None:
        try:
            url, expires_in = self._service.tidal_begin_login()
        except Exception as exc:
            self._tidal_status_row.set_subtitle(f"Sign-in failed: {exc}")
            return

        expires_msg = f"Finish within about {int(expires_in)} seconds."
        body = (
            "Choose Open in browser, then log in with your TIDAL account and "
            "approve access. This window updates when you are done.\n\n"
            "If the TIDAL page shows a letter code, you can ignore it—no need to "
            "type it into tunes-player. "
            f"{expires_msg}"
        )

        self._tidal_status_row.set_subtitle("Waiting for browser sign-in…")
        if self._tidal_oauth_poll_id == 0:
            self._tidal_oauth_poll_id = GLib.timeout_add(500, self._poll_tidal_oauth)

        dialog = Adw.AlertDialog(
            heading="Sign in to TIDAL",
            body=body,
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("copy", "Copy link")
        dialog.add_response("open", "Open in browser")
        dialog.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("open")
        dialog.set_close_response("cancel")
        self._tidal_sign_in_dialog = dialog

        def on_dialog_closed(_dlg: Adw.AlertDialog) -> None:
            self._tidal_sign_in_dialog_presented = False
            if self._tidal_sign_in_dialog is _dlg:
                self._tidal_sign_in_dialog = None

        def on_response(_dlg: Adw.AlertDialog, response: str) -> None:
            if response == "cancel":
                self._tidal_sign_in_dialog_presented = False
                self._tidal_sign_in_dialog = None
                if self._tidal_oauth_poll_id != 0:
                    GLib.source_remove(self._tidal_oauth_poll_id)
                    self._tidal_oauth_poll_id = 0
                self._service.tidal_cancel_login()
                self._reload_tidal_status()
                return
            if response == "copy":
                self._copy_text(url)
                return
            if response == "open":
                ok, err = open_external_uri(url)
                if not ok:
                    err_dialog = Adw.AlertDialog(
                        heading="Could not open browser",
                        body=f"{err or 'Unknown error'}\n\nOpen this link manually:\n{url}",
                    )
                    err_dialog.add_response("close", "Close")
                    err_dialog.present(self)

        dialog.connect("closed", on_dialog_closed)
        dialog.connect("response", on_response)
        dialog.present(self)
        self._tidal_sign_in_dialog_presented = True

    def _dismiss_tidal_sign_in_dialog(self) -> None:
        if not self._tidal_sign_in_dialog_presented:
            self._tidal_sign_in_dialog = None
            return
        dialog = self._tidal_sign_in_dialog
        self._tidal_sign_in_dialog = None
        self._tidal_sign_in_dialog_presented = False
        if dialog is not None:
            dialog.close()

    def _poll_tidal_oauth(self) -> bool:
        status = self._service.tidal_poll_login()
        if status == "pending":
            return True
        self._tidal_oauth_poll_id = 0
        if status == "success":
            self._dismiss_tidal_sign_in_dialog()
            self._reload_tidal_status()
            self._service.notify_sources_changed()
        elif status == "failed":
            self._dismiss_tidal_sign_in_dialog()
            err = self._service.tidal_oauth_error()
            self._tidal_status_row.set_subtitle(err or "Sign-in failed or timed out")
            self._reload_tidal_status()
        return False

    def _on_tidal_sign_out_clicked(self, *_args: object) -> None:
        self._service.tidal_logout()
        self._reload_tidal_status()
