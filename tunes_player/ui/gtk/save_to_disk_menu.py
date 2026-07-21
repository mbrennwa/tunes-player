"""Right-click Save to disk… (and release Labels…) for streaming/local releases."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Gio", "2.0")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from tunes_player.core.models import Source, Track
from tunes_player.core.release_quality import playback_preference_for_tier
from tunes_player.core.release_quality_tiles import parse_quality_tier_suffix
from tunes_player.core.save_to_disk import SaveToDiskError, is_writable_dir
from tunes_player.core.services import PlayerService
from tunes_player.ui.gtk.errors import show_error_toast, show_toast
from tunes_player.ui.gtk.release_label_menu import ReleaseLabelEditor

STREAMING_SOURCES = frozenset({Source.TIDAL, Source.QOBUZ})


def is_streaming_source(source: Source | str | None) -> bool:
    if isinstance(source, Source):
        return source in STREAMING_SOURCES
    if isinstance(source, str):
        key = source.casefold()
        return key in {"tidal", "qobuz"} or key.startswith(("tidal:", "qobuz:"))
    return False


def attach_release_context_menu(
    widget: Gtk.Widget,
    *,
    service: PlayerService,
    release_id: str,
    source: Source,
    on_label_changed: Callable[[], None] | None = None,
) -> None:
    """Secondary-click: Save to disk… (streaming) + Labels…"""
    label_popover: ReleaseLabelEditor | None = None

    def _open_labels() -> None:
        nonlocal label_popover
        if label_popover is None:
            label_popover = ReleaseLabelEditor(
                service=service,
                release_id=release_id,
                on_changed=on_label_changed,
            )
            label_popover.set_parent(widget)
        else:
            label_popover._release_id = release_id
            label_popover._rebuild_checks()
        label_popover.popup()

    gesture = Gtk.GestureClick()
    gesture.set_button(Gdk.BUTTON_SECONDARY)

    def _on_pressed(_gesture: Gtk.GestureClick, _n_press: int, x: float, y: float) -> None:
        if _picked_has_css_class(widget, x, y, "release-art-play"):
            return
        if _picked_has_css_class(widget, x, y, "artist-link"):
            return
        actions: list[tuple[str, Callable[[], None]]] = []
        if is_streaming_source(source):
            window = _find_window(widget)
            overlay = _toast_overlay_from(widget)
            actions.append(
                (
                    "Save to disk…",
                    lambda: begin_save_release(
                        service,
                        release_id=release_id,
                        toast_overlay=overlay,
                        parent_window=window,
                    ),
                )
            )
        actions.append(("Labels…", _open_labels))
        _show_action_popover(widget, x, y, actions=actions)

    gesture.connect("pressed", _on_pressed)
    widget.add_controller(gesture)


def attach_track_save_menu(
    row: Gtk.Widget,
    *,
    service: PlayerService,
    track: Track,
    toast_overlay: object | None = None,
    parent_window: Gtk.Window | None = None,
) -> None:
    if not is_streaming_source(track.source):
        return

    gesture = Gtk.GestureClick()
    gesture.set_button(Gdk.BUTTON_SECONDARY)

    def _on_pressed(_gesture: Gtk.GestureClick, _n_press: int, x: float, y: float) -> None:
        overlay = toast_overlay or _toast_overlay_from(row)
        window = parent_window or _find_window(row)
        _show_action_popover(
            row,
            x,
            y,
            actions=(
                (
                    "Save to disk…",
                    lambda: begin_save_tracks(
                        service,
                        tracks=[track],
                        toast_overlay=overlay,
                        parent_window=window,
                    ),
                ),
            ),
        )

    gesture.connect("pressed", _on_pressed)
    row.add_controller(gesture)


def begin_save_release(
    service: PlayerService,
    *,
    release_id: str,
    toast_overlay: object | None,
    parent_window: Gtk.Window | None,
) -> None:
    tile_tier = parse_quality_tier_suffix(release_id) or ""
    tracks = service.get_release_tracks(
        release_id,
        playback_preference=playback_preference_for_tier(tile_tier),
    )
    streaming = [t for t in tracks if is_streaming_source(t.source)]
    if not streaming:
        if toast_overlay is not None:
            show_error_toast(toast_overlay, "No streaming tracks to save.")
        return
    begin_save_tracks(
        service,
        tracks=streaming,
        toast_overlay=toast_overlay,
        parent_window=parent_window,
    )


def begin_save_tracks(
    service: PlayerService,
    *,
    tracks: Sequence[Track],
    toast_overlay: object | None,
    parent_window: Gtk.Window | None,
) -> None:
    if service.is_saving_to_disk():
        if toast_overlay is not None:
            show_error_toast(toast_overlay, "A download is already in progress.")
        return
    streaming = [t for t in tracks if is_streaming_source(t.source)]
    if not streaming:
        if toast_overlay is not None:
            show_error_toast(toast_overlay, "No streaming tracks to save.")
        return

    writable = _writable_music_folders(service)
    if len(writable) == 1:
        _start_save(
            service,
            tracks=list(streaming),
            dest=writable[0],
            toast_overlay=toast_overlay,
        )
        return
    if writable:
        _choose_music_folder_and_start(
            service,
            tracks=list(streaming),
            folders=writable,
            toast_overlay=toast_overlay,
            parent_window=parent_window,
        )
        return
    _choose_folder_via_file_dialog(
        service,
        tracks=list(streaming),
        toast_overlay=toast_overlay,
        parent_window=parent_window,
    )


def attach_download_toasts(overlay: object, service: PlayerService) -> None:
    """Show save-to-disk progress and results on an Adw.ToastOverlay."""

    def on_event(event: str) -> bool:
        if event == "download_started":
            show_toast(overlay, "Saving to disk…")
        elif event == "download_progress":
            progress = service.download_progress
            if progress is not None:
                current, total, title = progress
                show_toast(overlay, f"Saving {current}/{total}: {title}")
        elif event == "download_finished":
            count = service.download_saved_count
            err = service.download_last_error
            if err:
                show_error_toast(overlay, err)
            else:
                show_toast(
                    overlay,
                    f"Saved {count} track{'s' if count != 1 else ''}.",
                )
        elif event == "download_cancelled":
            show_toast(overlay, "Download cancelled")
        elif event == "download_error":
            show_error_toast(
                overlay,
                service.download_last_error or "Save to disk failed.",
            )
        return False

    service.subscribe(lambda event: GLib.idle_add(on_event, event))


def _writable_music_folders(service: PlayerService) -> list[Path]:
    """Configured music folders that exist and are writable, last-used first."""
    folders: list[Path] = []
    seen: set[str] = set()
    last = service.config.config.last_save_folder
    candidates: list[str] = []
    if last:
        candidates.append(last)
    candidates.extend(service.config.config.music_folders)
    configured = {
        str(Path(item).expanduser().resolve())
        for item in service.config.config.music_folders
    }
    for raw in candidates:
        try:
            path = Path(raw).expanduser().resolve()
        except OSError:
            continue
        key = str(path)
        if key in seen or key not in configured:
            continue
        seen.add(key)
        if path.is_dir() and is_writable_dir(path):
            folders.append(path)
    return folders


def _choose_music_folder_and_start(
    service: PlayerService,
    *,
    tracks: list[Track],
    folders: Sequence[Path],
    toast_overlay: object | None,
    parent_window: Gtk.Window | None,
) -> None:
    dialog = Adw.AlertDialog(
        heading="Save to disk",
        body="Choose a music folder:",
    )
    dialog.add_response("cancel", "Cancel")
    dialog.set_close_response("cancel")

    list_box = Gtk.ListBox()
    list_box.set_selection_mode(Gtk.SelectionMode.NONE)
    list_box.add_css_class("boxed-list")
    list_box.set_margin_top(6)

    def _pick(path: Path) -> None:
        dialog.force_close()
        _start_save(
            service,
            tracks=tracks,
            dest=path,
            toast_overlay=toast_overlay,
        )

    for folder in folders:
        row = Adw.ActionRow(
            title=folder.name or str(folder),
            subtitle=str(folder),
        )
        row.set_activatable(True)
        row.add_prefix(Gtk.Image.new_from_icon_name("folder-symbolic"))
        list_box.append(row)

    list_box.connect(
        "row-activated",
        lambda _box, row: _pick(folders[row.get_index()]),
    )
    dialog.set_extra_child(list_box)
    dialog.present(parent_window)


def _choose_folder_via_file_dialog(
    service: PlayerService,
    *,
    tracks: list[Track],
    toast_overlay: object | None,
    parent_window: Gtk.Window | None,
) -> None:
    dialog = Gtk.FileDialog(title="Save to folder")
    initial = _initial_save_folder(service)
    if initial is not None:
        dialog.set_initial_folder(Gio.File.new_for_path(str(initial)))

    def _on_selected(_dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        path = folder.get_path() if folder is not None else None
        if not path:
            return
        dest = Path(path)
        if not is_writable_dir(dest):
            if toast_overlay is not None:
                show_error_toast(toast_overlay, f"Folder is not writable: {dest}")
            return
        _start_save(
            service,
            tracks=tracks,
            dest=dest,
            toast_overlay=toast_overlay,
        )

    dialog.select_folder(parent_window, None, _on_selected)


def _start_save(
    service: PlayerService,
    *,
    tracks: list[Track],
    dest: Path,
    toast_overlay: object | None,
) -> None:
    service.set_last_save_folder(str(dest))
    try:
        service.start_save_to_disk(tracks=list(tracks), dest_dir=str(dest))
    except SaveToDiskError as exc:
        if toast_overlay is not None:
            show_error_toast(toast_overlay, str(exc))


def _initial_save_folder(service: PlayerService) -> Path | None:
    last = service.config.config.last_save_folder
    if last:
        path = Path(last).expanduser()
        if path.is_dir():
            return path
    folders = service.config.config.music_folders
    if folders:
        path = Path(folders[0]).expanduser()
        if path.is_dir():
            return path
    return None


def _show_action_popover(
    parent: Gtk.Widget,
    x: float,
    y: float,
    *,
    actions: Sequence[tuple[str, Callable[[], None]]],
) -> None:
    popover = Gtk.Popover()
    popover.set_parent(parent)
    popover.set_autohide(True)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    popover.set_child(box)
    for label, callback in actions:
        button = Gtk.Button(label=label)
        button.set_halign(Gtk.Align.FILL)
        button.add_css_class("flat")
        button.set_margin_start(6)
        button.set_margin_end(6)
        button.set_margin_top(4)
        button.set_margin_bottom(4)

        def _on_clicked(
            *_args: object,
            action: Callable[[], None] = callback,
            pop: Gtk.Popover = popover,
        ) -> None:
            pop.popdown()
            action()

        button.connect("clicked", _on_clicked)
        box.append(button)
    rect = Gdk.Rectangle()
    rect.x = int(x)
    rect.y = int(y)
    rect.width = 1
    rect.height = 1
    popover.set_pointing_to(rect)
    popover.popup()


def _picked_has_css_class(widget: Gtk.Widget, x: float, y: float, css_class: str) -> bool:
    picked = widget.pick(x, y, Gtk.PickFlags.DEFAULT)
    while picked is not None and picked is not widget:
        if picked.has_css_class(css_class):
            return True
        picked = picked.get_parent()
    return False


def _find_window(widget: Gtk.Widget) -> Gtk.Window | None:
    root = widget.get_root()
    if isinstance(root, Gtk.Window):
        return root
    return None


def _toast_overlay_from(widget: Gtk.Widget) -> object | None:
    window = _find_window(widget)
    if window is None:
        return None
    return getattr(window, "_toast_overlay", None)
