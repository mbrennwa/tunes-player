"""Load album art into Gtk.Image from source-agnostic art_uri."""

from __future__ import annotations

import threading
from pathlib import Path
from urllib.request import Request, urlopen

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from tunes_player.core.art import find_cached_art_path, parse_art_uri

FALLBACK_ICON = "audio-x-generic-symbolic"


class ArtLoader:
    """Resolve art_uri values into scaled Gtk.Image content."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._generation: dict[int, int] = {}
        self._next_generation = 0

    def set_image(
        self,
        image: Gtk.Image,
        art_uri: str | None,
        *,
        pixel_size: int,
        fallback_icon: str = FALLBACK_ICON,
    ) -> None:
        generation = self._next_generation
        self._next_generation += 1
        widget_id = id(image)
        self._generation[widget_id] = generation

        if not art_uri:
            self._apply_fallback(image, pixel_size, fallback_icon, widget_id, generation)
            return

        kind, payload = parse_art_uri(art_uri)
        if kind == "local":
            path = find_cached_art_path(self._data_dir, payload)
            if path is None:
                self._apply_fallback(image, pixel_size, fallback_icon, widget_id, generation)
                return
            threading.Thread(
                target=self._load_file,
                args=(path, image, pixel_size, widget_id, generation, fallback_icon),
                daemon=True,
            ).start()
            return
        if kind == "http":
            threading.Thread(
                target=self._load_http,
                args=(payload, image, pixel_size, widget_id, generation, fallback_icon),
                daemon=True,
            ).start()
            return
        self._apply_fallback(image, pixel_size, fallback_icon, widget_id, generation)

    def _is_current(self, widget_id: int, generation: int) -> bool:
        return self._generation.get(widget_id) == generation

    def _apply_fallback(
        self,
        image: Gtk.Image,
        pixel_size: int,
        fallback_icon: str,
        widget_id: int,
        generation: int,
    ) -> None:
        def apply() -> None:
            if not self._is_current(widget_id, generation):
                return
            image.clear()
            image.set_from_icon_name(fallback_icon)
            image.set_pixel_size(pixel_size)

        GLib.idle_add(apply)

    def _load_file(
        self,
        path: Path,
        image: Gtk.Image,
        pixel_size: int,
        widget_id: int,
        generation: int,
        fallback_icon: str,
    ) -> None:
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(path),
                pixel_size,
                pixel_size,
                True,
            )
        except GLib.Error:
            self._apply_fallback(image, pixel_size, fallback_icon, widget_id, generation)
            return
        GLib.idle_add(
            self._apply_pixbuf,
            image,
            pixbuf,
            pixel_size,
            widget_id,
            generation,
            fallback_icon,
        )

    def _load_http(
        self,
        url: str,
        image: Gtk.Image,
        pixel_size: int,
        widget_id: int,
        generation: int,
        fallback_icon: str,
    ) -> None:
        try:
            request = Request(url, headers={"User-Agent": "Tunes/0.1"})
            with urlopen(request, timeout=15) as response:
                data = response.read()
            loader = GdkPixbuf.PixbufLoader.new()
            loader.write(data)
            loader.close()
            pixbuf = loader.get_pixbuf()
            if pixbuf is None:
                raise ValueError("empty image")
            pixbuf = pixbuf.scale_simple(
                pixel_size,
                pixel_size,
                GdkPixbuf.InterpType.BILINEAR,
            )
        except (OSError, ValueError, GLib.Error):
            self._apply_fallback(image, pixel_size, fallback_icon, widget_id, generation)
            return
        GLib.idle_add(
            self._apply_pixbuf,
            image,
            pixbuf,
            pixel_size,
            widget_id,
            generation,
            fallback_icon,
        )

    def _apply_pixbuf(
        self,
        image: Gtk.Image,
        pixbuf: GdkPixbuf.Pixbuf,
        pixel_size: int,
        widget_id: int,
        generation: int,
        fallback_icon: str,
    ) -> bool:
        if not self._is_current(widget_id, generation):
            return False
        try:
            texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        except (AttributeError, TypeError, GLib.Error):
            self._apply_fallback(image, pixel_size, fallback_icon, widget_id, generation)
            return False
        image.clear()
        image.set_from_paintable(texture)
        image.set_size_request(pixel_size, pixel_size)
        return False
