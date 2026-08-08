"""MPRIS D-Bus media player integration for GNOME and other desktop shells."""

from __future__ import annotations

from pathlib import Path

import re
from collections.abc import Callable

import tunes_player.gi_bootstrap  # noqa: F401 — before gi.repository
import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")

from gi.repository import Gio, GLib  # noqa: E402

from tunes_player.core.art import resolve_art_url  # noqa: E402
from tunes_player.core.services import PlaybackState, PlayerService  # noqa: E402

BUS_NAME = "org.mpris.MediaPlayer2.tunes_player"
OBJECT_PATH = "/org/mpris/MediaPlayer2"
ROOT_INTERFACE = "org.mpris.MediaPlayer2"
PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
DESKTOP_ENTRY = "tunes.player"
IDENTITY = "Tunes"
SUPPORTED_MIME_TYPES = [
    "audio/flac",
    "audio/wav",
    "audio/x-wav",
    "audio/aiff",
    "audio/x-aiff",
    "audio/mp4",
    "audio/mpeg",
    "audio/aac",
    "audio/ogg",
    "application/ogg",
]

_INTROSPECTION = """
<node>
  <interface name="org.freedesktop.DBus.Properties">
    <method name="Get">
      <arg name="interface_name" type="s" direction="in"/>
      <arg name="property_name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="GetAll">
      <arg name="interface_name" type="s" direction="in"/>
      <arg name="properties" type="a{sv}" direction="out"/>
    </method>
    <method name="Set">
      <arg name="interface_name" type="s" direction="in"/>
      <arg name="property_name" type="s" direction="in"/>
      <arg name="value" type="v" direction="in"/>
    </method>
  </interface>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/>
    <method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Next"/>
    <method name="Previous"/>
    <method name="Pause"/>
    <method name="PlayPause"/>
    <method name="Stop"/>
    <method name="Play"/>
    <method name="Seek">
      <arg name="Offset" type="x" direction="in"/>
    </method>
    <method name="SetVolume">
      <arg name="Volume" type="d" direction="in"/>
    </method>
    <signal name="Seeked">
      <arg name="Position" type="x"/>
    </signal>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="LoopStatus" type="s" access="readwrite"/>
    <property name="Rate" type="d" access="readwrite"/>
    <property name="Shuffle" type="b" access="readwrite"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
  </interface>
</node>
"""

_VOID_REPLY = GLib.Variant("()", ())


def _track_object_path(track_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]", "_", track_id)
    return f"{OBJECT_PATH}/track/{safe}"


class MprisService:
    """Expose PlayerService over the MPRIS2 D-Bus interface."""

    def __init__(
        self,
        service: PlayerService,
        *,
        on_raise: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self._service = service
        self._on_raise = on_raise
        self._on_quit = on_quit
        self._connection: Gio.DBusConnection | None = None
        self._bus_name_id = 0
        self._registration_ids: list[int] = []
        self._unsubscribe: Callable[[], None] | None = None
        self._loop_status = "None"
        self._shuffle = False
        self._pending_property_names: set[str] = set()
        self._pending_property_emit = False
        self._position_timer_id = 0
        self._node_info = Gio.DBusNodeInfo.new_for_xml(_INTROSPECTION)

    def start(self) -> None:
        if self._bus_name_id != 0:
            return
        self._bus_name_id = Gio.bus_own_name(
            Gio.BusType.SESSION,
            BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            None,
            self._on_name_lost,
        )
        self._unsubscribe = self._service.subscribe(self._on_service_event)
        if self._position_timer_id == 0:
            self._position_timer_id = GLib.timeout_add_seconds(
                1,
                self._emit_position_if_playing,
            )

    def stop(self) -> None:
        if self._position_timer_id != 0:
            GLib.source_remove(self._position_timer_id)
            self._position_timer_id = 0
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        for registration_id in self._registration_ids:
            if self._connection is not None:
                self._connection.unregister_object(registration_id)
        self._registration_ids.clear()
        if self._bus_name_id != 0:
            Gio.bus_unown_name(self._bus_name_id)
            self._bus_name_id = 0
        self._connection = None

    def _on_bus_acquired(self, connection: Gio.DBusConnection, _name: str, *_args: object) -> None:
        self._connection = connection
        for interface in self._node_info.interfaces:
            registration_id = connection.register_object(
                OBJECT_PATH,
                interface,
                self._handle_method_call,
                self._handle_get_property,
                self._handle_set_property,
            )
            self._registration_ids.append(registration_id)
        self._schedule_player_properties(
            "PlaybackStatus",
            "Metadata",
            "CanGoNext",
            "CanGoPrevious",
            "CanPlay",
            "CanPause",
            "CanControl",
            "Volume",
            "Position",
        )

    def _on_name_lost(self, *_args: object) -> None:
        self.stop()

    def _on_service_event(self, event: str) -> None:
        GLib.idle_add(self._handle_service_event, event)

    def _handle_service_event(self, event: str) -> bool:
        if event in {"playback_changed", "queue_changed", "library_updated"}:
            self._schedule_player_properties(
                "PlaybackStatus",
                "Metadata",
                "CanGoNext",
                "CanGoPrevious",
                "CanPlay",
                "CanPause",
                "CanControl",
                "Position",
            )
        elif event == "volume_changed":
            self._schedule_player_properties("Volume")
        return False

    def _emit_position_if_playing(self) -> bool:
        state = self._service.get_playback_state()
        if state.is_playing:
            self._schedule_player_properties("Position")
        return True

    def _handle_method_call(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _object_path: str,
        interface_name: str,
        method_name: str,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        try:
            if interface_name == PROPERTIES_INTERFACE:
                self._handle_properties_method(method_name, parameters, invocation)
            elif interface_name == ROOT_INTERFACE:
                self._handle_root_method(method_name, invocation)
            elif interface_name == PLAYER_INTERFACE:
                self._handle_player_method(method_name, parameters, invocation)
            else:
                invocation.return_dbus_error(
                    "org.freedesktop.DBus.Error.UnknownMethod",
                    f"Unknown interface {interface_name}",
                )
        except GLib.Error as exc:
            invocation.return_gerror(exc)

    def _handle_properties_method(
        self,
        method_name: str,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        if method_name == "Get":
            interface_name, property_name = parameters.unpack()
            value = self._get_property_variant(str(interface_name), str(property_name))
            invocation.return_value(GLib.Variant("(v)", (value,)))
        elif method_name == "GetAll":
            (interface_name,) = parameters.unpack()
            properties = self._get_all_properties(str(interface_name))
            invocation.return_value(GLib.Variant("(a{sv})", (properties,)))
        elif method_name == "Set":
            interface_name, property_name, value = parameters.unpack()
            self._set_property_value(str(interface_name), str(property_name), value)
            invocation.return_value(_VOID_REPLY)
            if str(interface_name) == PLAYER_INTERFACE:
                self._schedule_player_properties(str(property_name))
        else:
            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.UnknownMethod",
                f"Unknown method {method_name}",
            )

    def _handle_root_method(self, method_name: str, invocation: Gio.DBusMethodInvocation) -> None:
        if method_name == "Raise":
            self._on_raise()
            invocation.return_value(_VOID_REPLY)
        elif method_name == "Quit":
            self._on_quit()
            invocation.return_value(_VOID_REPLY)
        else:
            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.UnknownMethod",
                f"Unknown method {method_name}",
            )

    def _handle_player_method(
        self,
        method_name: str,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        if method_name == "Next":
            self._service.skip_next()
        elif method_name == "Previous":
            self._service.skip_previous()
        elif method_name == "Pause":
            self._service.pause()
        elif method_name == "PlayPause":
            self._service.toggle_play_pause()
        elif method_name == "Stop":
            self._service.pause()
        elif method_name == "Play":
            self._service.play()
        elif method_name == "Seek":
            (offset_us,) = parameters.unpack()
            self._service.seek(max(0.0, offset_us / 1_000_000))
            self._emit_seeked(int(offset_us))
        elif method_name == "SetVolume":
            (volume,) = parameters.unpack()
            if self._service.volume_control_enabled():
                self._service.set_volume(max(0.0, min(1.0, volume)))
        else:
            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.UnknownMethod",
                f"Unknown method {method_name}",
            )
            return
        invocation.return_value(_VOID_REPLY)
        self._schedule_player_properties(
            "PlaybackStatus",
            "CanGoNext",
            "CanGoPrevious",
            "CanPlay",
            "CanPause",
            "Position",
            "Volume",
            "Metadata",
        )

    def _handle_get_property(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _object_path: str,
        interface_name: str,
        property_name: str,
    ) -> GLib.Variant:
        return self._get_property_variant(interface_name, property_name)

    def _handle_set_property(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _object_path: str,
        interface_name: str,
        property_name: str,
        value: GLib.Variant,
    ) -> bool:
        self._set_property_value(interface_name, property_name, value)
        if interface_name == PLAYER_INTERFACE:
            self._schedule_player_properties(property_name)
        return True

    def _get_property_variant(self, interface_name: str, property_name: str) -> GLib.Variant:
        properties = self._get_all_properties(interface_name)
        if property_name not in properties:
            raise GLib.Error.new_literal(
                Gio.dbus_error_quark(),
                "org.freedesktop.DBus.Error.UnknownProperty",
                f"Unknown property {interface_name}.{property_name}",
            )
        return properties[property_name]

    def _get_all_properties(self, interface_name: str) -> dict[str, GLib.Variant]:
        if interface_name == ROOT_INTERFACE:
            return {
                "CanQuit": GLib.Variant("b", True),
                "CanRaise": GLib.Variant("b", True),
                "HasTrackList": GLib.Variant("b", False),
                "Identity": GLib.Variant("s", IDENTITY),
                "DesktopEntry": GLib.Variant("s", DESKTOP_ENTRY),
                "SupportedUriSchemes": GLib.Variant("as", ["file"]),
                "SupportedMimeTypes": GLib.Variant("as", SUPPORTED_MIME_TYPES),
            }
        if interface_name == PLAYER_INTERFACE:
            state = self._service.get_playback_state()
            return {
                "PlaybackStatus": GLib.Variant("s", self._playback_status(state)),
                "LoopStatus": GLib.Variant("s", self._loop_status),
                "Rate": GLib.Variant("d", 1.0),
                "Shuffle": GLib.Variant("b", self._shuffle),
                "Metadata": GLib.Variant("a{sv}", self._metadata(state)),
                "Volume": GLib.Variant(
                    "d",
                    state.volume if self._service.volume_control_enabled() else 1.0,
                ),
                "Position": GLib.Variant("x", self._position_us(state)),
                "MinimumRate": GLib.Variant("d", 1.0),
                "MaximumRate": GLib.Variant("d", 1.0),
                "CanGoNext": GLib.Variant("b", self._can_go_next(state)),
                "CanGoPrevious": GLib.Variant("b", self._can_go_previous(state)),
                "CanPlay": GLib.Variant("b", self._can_play(state)),
                "CanPause": GLib.Variant("b", self._can_pause(state)),
                "CanControl": GLib.Variant("b", True),
            }
        raise GLib.Error.new_literal(
            Gio.dbus_error_quark(),
            "org.freedesktop.DBus.Error.UnknownInterface",
            f"Unknown interface {interface_name}",
        )

    def _set_property_value(
        self,
        interface_name: str,
        property_name: str,
        value: GLib.Variant,
    ) -> None:
        if interface_name != PLAYER_INTERFACE:
            raise GLib.Error.new_literal(
                Gio.dbus_error_quark(),
                "org.freedesktop.DBus.Error.UnknownProperty",
                f"Property {interface_name}.{property_name} is not writable",
            )
        if property_name == "Volume":
            if self._service.volume_control_enabled():
                self._service.set_volume(max(0.0, min(1.0, value.unpack())))
        elif property_name == "LoopStatus":
            loop_status = value.unpack()
            if loop_status not in {"None", "Track", "Playlist"}:
                raise GLib.Error.new_literal(
                    Gio.dbus_error_quark(),
                    "org.mpris.MediaPlayer2.Error.NotSupported",
                    f"Loop status {loop_status} is not supported",
                )
            self._loop_status = loop_status
        elif property_name == "Rate":
            rate = value.unpack()
            if rate != 1.0:
                raise GLib.Error.new_literal(
                    Gio.dbus_error_quark(),
                    "org.mpris.MediaPlayer2.Error.NotSupported",
                    "Changing playback rate is not supported",
                )
        elif property_name == "Shuffle":
            self._shuffle = bool(value.unpack())
        else:
            raise GLib.Error.new_literal(
                Gio.dbus_error_quark(),
                "org.freedesktop.DBus.Error.PropertyReadOnly",
                f"Property {property_name} is read-only",
            )

    def _schedule_player_properties(self, *property_names: str) -> None:
        self._pending_property_names.update(property_names)
        if self._pending_property_emit:
            return
        self._pending_property_emit = True
        GLib.idle_add(self._flush_player_properties)

    def _flush_player_properties(self) -> bool:
        self._pending_property_emit = False
        if not self._pending_property_names:
            return False
        names = tuple(self._pending_property_names)
        self._pending_property_names.clear()
        self._emit_player_properties(*names)
        return False

    def _emit_player_properties(self, *property_names: str) -> bool:
        if self._connection is None:
            return False
        changed: dict[str, GLib.Variant] = {}
        all_props = self._get_all_properties(PLAYER_INTERFACE)
        for name in property_names:
            if name in all_props:
                changed[name] = all_props[name]
        if not changed:
            return False
        self._connection.emit_signal(
            None,
            OBJECT_PATH,
            PROPERTIES_INTERFACE,
            "PropertiesChanged",
            GLib.Variant("(sa{sv}as)", (PLAYER_INTERFACE, changed, [])),
        )
        return False

    def _emit_seeked(self, position_us: int) -> None:
        if self._connection is None:
            return
        self._connection.emit_signal(
            None,
            OBJECT_PATH,
            PLAYER_INTERFACE,
            "Seeked",
            GLib.Variant("(x)", (position_us,)),
        )

    @staticmethod
    def _playback_status(state: PlaybackState) -> str:
        track = state.current_track
        if track is None:
            return "Stopped"
        if state.is_playing:
            return "Playing"
        return "Paused"

    @staticmethod
    def _position_us(state: PlaybackState) -> int:
        return int(max(0.0, state.position_sec) * 1_000_000)

    @staticmethod
    def _can_go_next(state: PlaybackState) -> bool:
        return bool(state.queue) and state.queue_index + 1 < len(state.queue)

    @staticmethod
    def _can_go_previous(state: PlaybackState) -> bool:
        return bool(state.queue) and (state.queue_index > 0 or state.position_sec > 3.0)

    @staticmethod
    def _can_play(state: PlaybackState) -> bool:
        return state.current_track is not None or bool(state.queue)

    @staticmethod
    def _can_pause(state: PlaybackState) -> bool:
        return state.current_track is not None and state.is_playing

    def _metadata(self, state: PlaybackState) -> dict[str, GLib.Variant]:
        track = state.current_track
        if track is None:
            return {}
        metadata: dict[str, GLib.Variant] = {
            "mpris:trackid": GLib.Variant("o", _track_object_path(track.id)),
            "xesam:title": GLib.Variant("s", track.title),
            "xesam:artist": GLib.Variant("as", [track.artist_name]),
        }
        if track.release_title:
            metadata["xesam:album"] = GLib.Variant("s", track.release_title)
        if state.duration_sec is not None:
            metadata["xesam:duration"] = GLib.Variant("x", int(state.duration_sec * 1_000_000))
        # Never call resolve_track here: MPRIS property flushes run on the GTK
        # main loop and stream negotiation (esp. TIDAL 429 retries) freezes the UI.
        if track.id.startswith("local:"):
            file_meta = self._service.store.get_file_metadata(track.id)
            if file_meta is not None and file_meta.path:
                metadata["xesam:url"] = GLib.Variant(
                    "s",
                    Path(file_meta.path).resolve().as_uri(),
                )
        art_url = resolve_art_url(
            track.art_uri,
            data_dir=self._service.config.data_dir,
        )
        if art_url is not None:
            metadata["mpris:artUrl"] = GLib.Variant("s", art_url)
        return metadata


def create_mpris_service(
    service: PlayerService,
    *,
    on_raise: Callable[[], None],
    on_quit: Callable[[], None],
) -> MprisService:
    return MprisService(service, on_raise=on_raise, on_quit=on_quit)
