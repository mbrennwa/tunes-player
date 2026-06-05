"""Application preferences."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from tunes_player.core.audio_labels import endpoint_dropdown_label
from tunes_player.core.library import ScanResult
from tunes_player.core.services import PlayerService
from tunes_player.ui.gtk.util import escape_markup, open_external_uri, read_clipboard_text


class PreferencesWindow(Adw.PreferencesWindow):
    def __init__(self, *, parent: Adw.ApplicationWindow, service: PlayerService) -> None:
        super().__init__()
        self._service = service
        self._parent = parent
        self._dynamic_rows: list[Adw.ActionRow] = []
        self._folder_rows: dict[str, Adw.ActionRow] = {}
        self._folder_scan_buttons: dict[str, Gtk.Button] = {}
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

        sources_page = Adw.PreferencesPage(title="Sources", icon_name="folder-music-symbolic")
        local_group = Adw.PreferencesGroup(
            title="Local files",
            description="Folders are scanned into the local library index.",
        )
        local_group.add(self._folders_group)
        sources_page.add(local_group)

        audio = Adw.PreferencesGroup(title="Audio")
        self._audio_group = audio
        self._apply_audio_group_description()

        self._output_row = Adw.ActionRow(title="Output device")
        self._output_dropdown = Gtk.DropDown(model=Gtk.StringList.new([]))
        self._output_dropdown.set_halign(Gtk.Align.END)
        self._output_dropdown.set_size_request(260, -1)
        self._output_dropdown.set_valign(Gtk.Align.CENTER)
        self._output_dropdown.connect("notify::selected", self._on_output_changed)
        self._output_row.add_suffix(self._output_dropdown)
        self._output_row.set_activatable_widget(self._output_dropdown)
        audio.add(self._output_row)

        self._exclusive_row = Adw.SwitchRow(
            title="Exclusive device access",
            subtitle=(
                "Other apps can share the device; may prevent bit-perfect playback."
            ),
            active=service.config.config.exclusive_device_access,
        )
        self._exclusive_row.connect(
            "notify::active", self._on_exclusive_device_access_changed
        )
        audio.add(self._exclusive_row)

        self._software_volume_row = Adw.SwitchRow(
            title="Allow software volume",
            subtitle="For devices with fixed volume",
            active=service.config.config.allow_software_volume_fallback,
        )
        self._software_volume_row.connect(
            "notify::active", self._on_software_volume_fallback_changed
        )
        audio.add(self._software_volume_row)
        self._reload_output_sinks()

        audio_page = Adw.PreferencesPage(title="Audio", icon_name="audio-speakers-symbolic")
        audio_page.add(audio)

        application_group = Adw.PreferencesGroup(title="New Releases")
        days = service.config.config.new_music_within_days
        self._new_music_days_adj = Gtk.Adjustment.new(
            days,
            1,
            365,
            1,
            7,
            0,
        )
        self._new_music_days_row = Adw.SpinRow(
            title="Cutoff Days",
            subtitle="How far back to include releases in the New Releases search",
            adjustment=self._new_music_days_adj,
        )
        self._new_music_days_adj.connect("value-changed", self._on_new_music_within_days_changed)
        application_group.add(self._new_music_days_row)

        application_page = Adw.PreferencesPage(
            title="Application",
            icon_name="applications-system-symbolic",
        )
        application_page.add(application_group)

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
                "Requires your own TIDAL subscription. Sign-in opens your browser; "
                "copy the address bar afterward to finish connecting in Tunes."
            ),
        )
        tidal_group.add(self._tidal_status_row)
        sources_page.add(tidal_group)

        qobuz_group = Adw.PreferencesGroup(
            title="Qobuz",
            description=(
                "Requires your own Qobuz subscription, App ID, and App Secret "
                "(Tunes does not ship Qobuz credentials). Obtain them from Qobuz "
                "or for personal use from your own web-player inspection."
            ),
        )
        cfg = service.config.config
        self._qobuz_app_id_row = Adw.EntryRow(title="App ID")
        if cfg.qobuz_app_id:
            self._qobuz_app_id_row.set_text(cfg.qobuz_app_id)
        qobuz_group.add(self._qobuz_app_id_row)

        self._qobuz_app_secret_row = Adw.PasswordEntryRow(title="App Secret")
        if cfg.qobuz_app_secret:
            self._qobuz_app_secret_row.set_text(cfg.qobuz_app_secret)
        qobuz_group.add(self._qobuz_app_secret_row)

        self._qobuz_save_creds_row = Adw.ActionRow(
            title="Save Qobuz credentials",
            subtitle="Required before sign-in",
        )
        qobuz_save_btn = Gtk.Button(label="Save")
        qobuz_save_btn.set_valign(Gtk.Align.CENTER)
        qobuz_save_btn.connect("clicked", self._on_qobuz_save_credentials_clicked)
        self._qobuz_save_creds_row.add_suffix(qobuz_save_btn)
        self._qobuz_save_creds_row.set_activatable_widget(qobuz_save_btn)
        qobuz_group.add(self._qobuz_save_creds_row)

        self._qobuz_email_row = Adw.EntryRow(title="Email")
        qobuz_group.add(self._qobuz_email_row)

        self._qobuz_password_row = Adw.PasswordEntryRow(title="Password")
        qobuz_group.add(self._qobuz_password_row)

        self._qobuz_status_row = Adw.ActionRow(title="Account")
        self._qobuz_sign_in_btn = Gtk.Button(label="Sign in")
        self._qobuz_sign_in_btn.connect("clicked", self._on_qobuz_sign_in_clicked)
        self._qobuz_sign_out_btn = Gtk.Button(label="Sign out")
        self._qobuz_sign_out_btn.connect("clicked", self._on_qobuz_sign_out_clicked)
        qobuz_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        qobuz_btn_box.append(self._qobuz_sign_in_btn)
        qobuz_btn_box.append(self._qobuz_sign_out_btn)
        qobuz_btn_box.set_valign(Gtk.Align.CENTER)
        self._qobuz_status_row.add_suffix(qobuz_btn_box)
        qobuz_group.add(self._qobuz_status_row)
        sources_page.add(qobuz_group)

        diagnostics_page = Adw.PreferencesPage(
            title="Diagnostics",
            icon_name="utilities-terminal-symbolic",
        )
        diagnostics_page.add(diagnostics_group)

        self.add(application_page)
        self.add(sources_page)
        self.add(audio_page)
        self.add(diagnostics_page)
        self.set_visible_page(sources_page)

        self._tidal_pkce_dialog: Adw.Dialog | None = None
        self._updating_output_dropdown = False
        self._reload_folders()
        self._reload_tidal_status()
        self._reload_qobuz_status()
        service.subscribe(lambda event: GLib.idle_add(self._on_service_event, event))
        self.connect("map", self._on_preferences_map)

    def _on_preferences_map(self, *_args: object) -> None:
        self._apply_audio_group_description()
        self._reload_output_sinks()
        self._sync_software_volume_row()
        self._sync_scan_ui()

    @staticmethod
    def _folder_key(folder: str) -> str:
        return str(Path(folder).expanduser().resolve())

    def _reload_folders(self) -> None:
        for row in self._dynamic_rows:
            self._folders_group.remove(row)
        self._dynamic_rows.clear()
        self._folder_rows.clear()
        self._folder_scan_buttons.clear()

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
                folder_key = self._folder_key(folder)
                row = Adw.ActionRow(
                    title=escape_markup(folder),
                    subtitle="Local music library",
                )
                scan_button = Gtk.Button(label="Scan")
                scan_button.set_valign(Gtk.Align.CENTER)
                scan_button.connect(
                    "clicked",
                    lambda _btn, path=folder: self._on_scan_folder_clicked(path),
                )
                remove_button = Gtk.Button(icon_name="user-trash-symbolic")
                remove_button.set_valign(Gtk.Align.CENTER)
                remove_button.connect("clicked", lambda _btn, path=folder: self._remove_folder(path))
                btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                btn_box.set_valign(Gtk.Align.CENTER)
                btn_box.append(scan_button)
                btn_box.append(remove_button)
                row.add_suffix(btn_box)
                self._folders_group.add(row)
                self._dynamic_rows.append(row)
                self._folder_rows[folder_key] = row
                self._folder_scan_buttons[folder_key] = scan_button

        self._sync_scan_ui()

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

    def _on_software_volume_fallback_changed(self, row: Adw.SwitchRow, *_args: object) -> None:
        self._service.set_allow_software_volume_fallback(row.get_active())

    def _on_new_music_within_days_changed(self, adjustment: Gtk.Adjustment) -> None:
        days = int(adjustment.get_value())
        if days == self._service.config.config.new_music_within_days:
            return
        self._service.config.set_new_music_within_days(days)
        self._service.notify_sources_changed()

    def _apply_audio_group_description(self) -> None:
        stack = self._service.get_linux_audio_stack_info()
        if stack is None:
            self._audio_group.set_description(
                "Output applies to Tunes only (not the system default sink)."
            )
            return
        hint = stack.settings_hint.strip()
        if hint:
            self._audio_group.set_description(hint)
        else:
            self._audio_group.set_description(None)

    def _output_row_subtitle(self) -> str:
        state = self._service.get_playback_state()
        if state.output_using_fallback:
            return "Saved device unavailable — using fallback"
        return ""

    def _sync_software_volume_row(self) -> None:
        """Disable soft-volume option when the selected output has hardware/sink volume."""
        row = getattr(self, "_software_volume_row", None)
        if row is None:
            return
        state = self._service.get_playback_state()
        has_device_volume = state.device_volume
        row.set_sensitive(not has_device_volume)
        row.handler_block_by_func(self._on_software_volume_fallback_changed)
        if has_device_volume:
            row.set_active(False)
        else:
            row.set_active(self._service.config.config.allow_software_volume_fallback)
        row.handler_unblock_by_func(self._on_software_volume_fallback_changed)

    def _sync_exclusive_row(self) -> None:
        row = getattr(self, "_exclusive_row", None)
        if row is None:
            return
        supported = self._service.exclusive_access_supported()
        row.set_sensitive(supported)
        row.handler_block_by_func(self._on_exclusive_device_access_changed)
        if supported:
            active = self._service.config.config.exclusive_device_access
            row.set_active(active)
            if active:
                row.set_subtitle("Other apps are paused while Tunes is playing.")
            else:
                row.set_subtitle(
                    "Other apps can share the device; may prevent bit-perfect playback."
                )
        else:
            row.set_active(False)
            row.set_subtitle("Exclusive access not supported on this device")
            if self._service.config.config.exclusive_device_access:
                self._service.set_exclusive_device_access(False)
        row.handler_unblock_by_func(self._on_exclusive_device_access_changed)

    def _on_exclusive_device_access_changed(self, row: Adw.SwitchRow, *_args: object) -> None:
        self._service.set_exclusive_device_access(row.get_active())
        self._sync_exclusive_row()

    def _reload_output_sinks(self) -> None:
        self._updating_output_dropdown = True
        try:
            self._output_dropdown.handler_block_by_func(self._on_output_changed)
            endpoints = self._service.list_output_sinks()
            if not endpoints:
                self._output_row.set_sensitive(False)
                self._output_row.set_subtitle("No controllable sinks found")
                self._output_dropdown.set_model(Gtk.StringList.new([]))
                self._sync_software_volume_row()
                self._sync_exclusive_row()
                return

            self._output_row.set_sensitive(True)
            names = [endpoint_dropdown_label(endpoint) for endpoint in endpoints]
            self._output_dropdown.set_model(Gtk.StringList.new(names))

            active_id = self._service.config.config.output_sink_id
            selected = 0
            for index, endpoint in enumerate(endpoints):
                if endpoint.id == active_id or (
                    active_id is None and endpoint.is_default
                ):
                    selected = index
                    break
            self._output_dropdown.set_selected(selected)
            self._output_row.set_subtitle(self._output_row_subtitle())
            self._sync_software_volume_row()
            self._sync_exclusive_row()
        finally:
            self._output_dropdown.handler_unblock_by_func(self._on_output_changed)
            self._updating_output_dropdown = False

    def _on_output_changed(self, dropdown: Gtk.DropDown, *_args: object) -> None:
        if self._updating_output_dropdown:
            return
        endpoints = self._service.list_output_sinks()
        index = dropdown.get_selected()
        if index < 0 or index >= len(endpoints):
            return
        endpoint = endpoints[index]
        if endpoint.id == self._service.config.config.output_sink_id:
            return
        self._service.set_output_sink(endpoint.id)
        self._output_row.set_subtitle(self._output_row_subtitle())
        self._sync_software_volume_row()
        self._sync_exclusive_row()

    def _on_scan_folder_clicked(self, folder: str) -> None:
        if self._service.is_scanning():
            folder_key = self._folder_key(folder)
            active_folder = self._service.scanning_folder
            if active_folder is not None and folder_key != active_folder:
                row = self._folder_rows.get(folder_key)
                if row is not None:
                    row.set_subtitle("Another folder is scanning")
            return
        folder_key = self._folder_key(folder)
        row = self._folder_rows.get(folder_key)
        if row is not None:
            row.set_subtitle("Starting scan…")
        scan_button = self._folder_scan_buttons.get(folder_key)
        if scan_button is not None:
            scan_button.set_sensitive(False)
        self._service.scan_library(folder=folder)

    def _format_scan_result(self, result: ScanResult) -> str:
        parts = [
            f"indexed {result.indexed}",
            f"skipped {result.skipped}",
            f"removed {result.removed}",
            f"errors {result.errors}",
        ]
        if result.art_indexed:
            parts.insert(1, f"art {result.art_indexed}")
        return f"Done — {', '.join(parts)}"

    def _format_scan_progress(self, current: int, total: int, path: str) -> str:
        if total == 0:
            text = path or "Discovering files…"
        else:
            name = path.rsplit("/", 1)[-1]
            text = f"Scanning {current}/{total}: {name}"
        return escape_markup(text)

    def _sync_scan_ui(self) -> None:
        scanning = self._service.is_scanning()
        active_folder = self._service.scanning_folder
        progress = self._service.scan_progress
        finished_folder = self._service.scan_finished_folder
        last_result = self._service.scan_last_result
        last_error = self._service.scan_last_error

        for folder_key, row in self._folder_rows.items():
            scan_button = self._folder_scan_buttons.get(folder_key)
            if scan_button is None:
                continue

            if scanning and active_folder == folder_key:
                scan_button.set_sensitive(False)
                if progress is not None:
                    row.set_subtitle(self._format_scan_progress(*progress))
                else:
                    row.set_subtitle("Starting scan…")
            elif scanning:
                scan_button.set_sensitive(False)
                row.set_subtitle("Another folder is scanning")
            elif finished_folder == folder_key and last_result is not None:
                scan_button.set_sensitive(True)
                row.set_subtitle(self._format_scan_result(last_result))
            elif finished_folder == folder_key and last_error is not None:
                scan_button.set_sensitive(True)
                row.set_subtitle(escape_markup(f"Scan failed: {last_error}"))
            else:
                scan_button.set_sensitive(True)
                row.set_subtitle("Local music library")

    def _copy_text(self, text: str) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        clipboard = display.get_clipboard()
        try:
            clipboard.set_content(Gdk.ContentProvider.new_for_string(text))
        except (AttributeError, TypeError):
            clipboard.set_content(
                Gdk.ContentProvider.new_for_bytes(
                    "text/plain;charset=utf-8",
                    GLib.Bytes.new(text.encode("utf-8")),
                )
            )

    def _copy_log_path(self, log_path: object) -> None:
        self._copy_text(str(log_path))

    def _on_service_event(self, event: str) -> bool:
        if event == "sources_changed":
            self._reload_tidal_status()
            self._reload_qobuz_status()
        elif event in ("playback_changed", "volume_changed"):
            self._sync_software_volume_row()
            self._sync_exclusive_row()
        elif event in ("scan_started", "scan_progress", "scan_finished", "scan_error"):
            self._sync_scan_ui()
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
            base = f"Connected as {label}" if label else "Connected"
            if service.tidal_needs_lossless_relogin():
                self._tidal_status_row.set_subtitle(
                    f"{base} — sign out and sign in again for lossless (CD) quality"
                )
            else:
                self._tidal_status_row.set_subtitle(base)
            self._tidal_sign_in_btn.set_sensitive(False)
            self._tidal_sign_out_btn.set_sensitive(True)
        else:
            self._tidal_status_row.set_subtitle("Not connected")
            self._tidal_sign_in_btn.set_sensitive(True)
            self._tidal_sign_out_btn.set_sensitive(False)

    def _on_tidal_sign_in_clicked(self, *_args: object) -> None:
        try:
            login_url = self._service.tidal_begin_pkce_login()
        except Exception as exc:
            self._tidal_status_row.set_subtitle(f"Sign-in failed: {exc}")
            return
        self._tidal_status_row.set_subtitle("Finish sign-in in the dialog…")
        self._present_tidal_pkce_sign_in(login_url)

    def _present_tidal_pkce_sign_in(self, login_url: str) -> None:
        dialog = Adw.Dialog()
        dialog.set_title("Connect TIDAL")
        dialog.set_content_width(480)
        dialog.set_content_height(440)
        dialog.add_css_class("tidal-pkce-signin")
        self._tidal_pkce_dialog = dialog

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(8)
        content.set_margin_bottom(8)
        content.set_margin_start(12)
        content.set_margin_end(12)

        intro = Gtk.Label(
            label="Sign in on TIDAL’s website, then paste the browser address here.",
            wrap=True,
            xalign=0,
        )
        intro.add_css_class("title-4")
        content.append(intro)

        steps = Gtk.ListBox()
        steps.add_css_class("boxed-list")
        steps.add_css_class("tidal-pkce-steps")
        steps.set_selection_mode(Gtk.SelectionMode.NONE)

        step1 = Adw.ActionRow(
            title="1. Sign in",
            subtitle="Your browser opens the TIDAL login page.",
        )
        open_again = Gtk.Button(label="Open again")
        open_again.add_css_class("flat")
        open_again.set_valign(Gtk.Align.CENTER)
        step1.add_suffix(open_again)
        steps.append(step1)

        step2 = Adw.ActionRow(
            title="2. Copy the address bar",
            subtitle=(
                "After sign-in, TIDAL shows “Page not found”—that is normal. "
                "Copy the full URL (it must contain code=)."
            ),
        )
        steps.append(step2)

        step3 = Adw.ActionRow(
            title="3. Paste below and connect",
            subtitle="Use the field under these steps.",
        )
        steps.append(step3)
        content.append(steps)

        url_label = Gtk.Label(
            label="Address from your browser",
            xalign=0,
        )
        url_label.add_css_class("heading")
        content.append(url_label)

        url_entry = Gtk.Entry()
        url_entry.set_placeholder_text("https://…?code=…")
        url_entry.set_hexpand(True)
        url_entry.add_css_class("tidal-pkce-url-entry")
        paste_btn = Gtk.Button(icon_name="edit-paste-symbolic")
        paste_btn.set_tooltip_text("Paste from clipboard")
        paste_btn.set_valign(Gtk.Align.CENTER)
        entry_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_top=8,
            margin_bottom=8,
            margin_start=12,
            margin_end=12,
        )
        entry_row.append(url_entry)
        entry_row.append(paste_btn)
        url_list = Gtk.ListBox()
        url_list.add_css_class("boxed-list")
        url_list.set_selection_mode(Gtk.SelectionMode.NONE)
        url_list_row = Gtk.ListBoxRow()
        url_list_row.set_child(entry_row)
        url_list.append(url_list_row)
        content.append(url_list)

        toolbar.set_content(content)

        bottom = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            margin_top=8,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        connect_btn = Gtk.Button(label="Connect")
        connect_btn.add_css_class("suggested-action")
        connect_btn.add_css_class("pill")
        connect_btn.set_halign(Gtk.Align.FILL)
        connect_btn.set_hexpand(True)
        bottom.append(connect_btn)
        toolbar.add_bottom_bar(bottom)

        dialog.set_child(toolbar)

        def close_dialog() -> None:
            if self._tidal_pkce_dialog is dialog:
                self._tidal_pkce_dialog = None
            dialog.close()

        def on_open(*_args: object) -> None:
            ok, err = open_external_uri(login_url)
            if not ok:
                self._tidal_status_row.set_subtitle(
                    err or "Could not open browser"
                )

        def on_paste(*_args: object) -> None:
            def apply_text(text: str | None) -> None:
                if text:
                    url_entry.set_text(text)
                    url_entry.grab_focus()

            read_clipboard_text(apply_text)

        def on_connect(*_args: object) -> None:
            redirect_url = url_entry.get_text().strip()
            if not redirect_url:
                url_entry.grab_focus()
                self._tidal_status_row.set_subtitle(
                    "Paste the address from your browser’s location bar."
                )
                return
            try:
                self._service.tidal_complete_pkce_login(redirect_url)
            except Exception as exc:
                self._tidal_status_row.set_subtitle(f"Sign-in failed: {exc}")
                return
            close_dialog()
            self._reload_tidal_status()
            self._service.notify_sources_changed()

        def on_closed(*_args: object) -> None:
            if self._tidal_pkce_dialog is dialog:
                self._tidal_pkce_dialog = None
            self._reload_tidal_status()

        open_again.connect("clicked", on_open)
        paste_btn.connect("clicked", on_paste)
        connect_btn.connect("clicked", on_connect)
        url_entry.connect("activate", on_connect)
        dialog.connect("closed", on_closed)
        dialog.present(self)

        def open_browser_and_focus_entry() -> bool:
            on_open()
            url_entry.grab_focus()
            return False

        GLib.idle_add(open_browser_and_focus_entry)

    def _on_tidal_sign_out_clicked(self, *_args: object) -> None:
        self._service.tidal_logout()
        self._reload_tidal_status()

    def _reload_qobuz_status(self) -> None:
        service = self._service
        creds_ok = service.qobuz_configured()
        self._qobuz_save_creds_row.set_subtitle(
            "Saved" if creds_ok else "Required before sign-in"
        )
        can_sign_in = creds_ok
        if service.qobuz_is_logged_in():
            label = service.qobuz_account_label()
            self._qobuz_status_row.set_subtitle(
                f"Connected as {label}" if label else "Connected"
            )
            self._qobuz_sign_in_btn.set_sensitive(False)
            self._qobuz_sign_out_btn.set_sensitive(True)
            self._qobuz_email_row.set_sensitive(False)
            self._qobuz_password_row.set_sensitive(False)
        else:
            if not creds_ok:
                self._qobuz_status_row.set_subtitle("Save App ID and App Secret first")
            else:
                self._qobuz_status_row.set_subtitle("Not connected")
            self._qobuz_sign_in_btn.set_sensitive(can_sign_in)
            self._qobuz_sign_out_btn.set_sensitive(False)
            self._qobuz_email_row.set_sensitive(True)
            self._qobuz_password_row.set_sensitive(True)

    def _on_qobuz_save_credentials_clicked(self, *_args: object) -> None:
        app_id = self._qobuz_app_id_row.get_text().strip()
        app_secret = self._qobuz_app_secret_row.get_text()
        if not app_id or not app_secret:
            self._qobuz_save_creds_row.set_subtitle("App ID and App Secret are both required")
            return
        try:
            self._service.qobuz_set_credentials(app_id, app_secret)
        except Exception as exc:
            self._qobuz_save_creds_row.set_subtitle(f"Save failed: {exc}")
            return
        self._qobuz_password_row.set_text("")
        self._reload_qobuz_status()

    def _on_qobuz_sign_in_clicked(self, *_args: object) -> None:
        email = self._qobuz_email_row.get_text().strip()
        password = self._qobuz_password_row.get_text()
        if not email or not password:
            self._qobuz_status_row.set_subtitle("Email and password are required")
            return
        try:
            self._service.qobuz_login(email, password)
        except Exception as exc:
            self._qobuz_status_row.set_subtitle(f"Sign-in failed: {exc}")
            return
        self._qobuz_password_row.set_text("")
        self._reload_qobuz_status()
        self._service.notify_sources_changed()

    def _on_qobuz_sign_out_clicked(self, *_args: object) -> None:
        self._service.qobuz_logout()
        self._reload_qobuz_status()
