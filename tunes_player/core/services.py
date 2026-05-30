"""Facade between UI and backends — expand as features land."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from tunes_player.core import demo_library
from tunes_player.core.models import Album, Artist, Track

EventCallback = Callable[[str], None]
Unsubscribe = Callable[[], None]


@dataclass(frozen=True, slots=True)
class SearchResults:
    albums: list[Album]
    tracks: list[Track]


@dataclass(frozen=True, slots=True)
class PlaybackState:
    current_track: Track | None
    is_playing: bool
    volume: float
    queue: tuple[Track, ...]
    queue_index: int
    quality_hint: str
    bit_perfect: bool
    device_volume: bool


class PlayerService:
    """Stable API for GTK (and future) frontends."""

    def __init__(self) -> None:
        self._listeners: list[EventCallback] = []
        self._volume = 0.72
        self._bit_perfect = True
        self._device_volume = True
        self._is_playing = False
        self._queue: list[Track] = []
        self._queue_index = -1
        self._current_track: Track | None = None
        self._quality_hint = ""

    def list_albums(self) -> list[Album]:
        return list(demo_library.demo_albums())

    def list_artists(self) -> list[Artist]:
        return list(demo_library.demo_artists())

    def get_album(self, album_id: str) -> Album | None:
        return demo_library.demo_album_by_id(album_id)

    def get_album_tracks(self, album_id: str) -> list[Track]:
        return list(demo_library.demo_tracks_for_album(album_id))

    def get_artist_albums(self, artist_id: str) -> list[Album]:
        artist = next((item for item in self.list_artists() if item.id == artist_id), None)
        if artist is None:
            return []
        return [album for album in self.list_albums() if album.artist_name == artist.name]

    def search(self, query: str) -> SearchResults:
        needle = query.strip().casefold()
        if not needle:
            return SearchResults(albums=[], tracks=[])

        albums = [
            album
            for album in self.list_albums()
            if needle in album.title.casefold() or needle in album.artist_name.casefold()
        ]
        tracks = [
            track
            for track in demo_library.demo_all_tracks()
            if needle in track.title.casefold()
            or needle in track.artist_name.casefold()
            or (track.album_title and needle in track.album_title.casefold())
        ]
        return SearchResults(albums=albums, tracks=tracks)

    def get_playback_state(self) -> PlaybackState:
        return PlaybackState(
            current_track=self._current_track,
            is_playing=self._is_playing,
            volume=self._volume,
            queue=tuple(self._queue),
            queue_index=self._queue_index,
            quality_hint=self._quality_hint,
            bit_perfect=self._bit_perfect,
            device_volume=self._device_volume,
        )

    def play_track(self, track_id: str) -> None:
        track = demo_library.demo_track_by_id(track_id)
        if track is None:
            return
        album_id = demo_library.demo_album_id_for_track(track_id)
        if album_id is not None:
            self._queue = list(demo_library.demo_tracks_for_album(album_id))
            self._queue_index = next(
                (index for index, item in enumerate(self._queue) if item.id == track_id),
                0,
            )
        else:
            self._queue = [track]
            self._queue_index = 0
        self._set_current_track(track)
        self._is_playing = True
        self._emit("playback_changed", "queue_changed")

    def play_album(self, album_id: str, *, start_index: int = 0) -> None:
        tracks = self.get_album_tracks(album_id)
        if not tracks:
            return
        start_index = max(0, min(start_index, len(tracks) - 1))
        self._queue = tracks
        self._queue_index = start_index
        self._set_current_track(tracks[start_index])
        self._is_playing = True
        self._emit("playback_changed", "queue_changed")

    def toggle_play_pause(self) -> None:
        if self._current_track is None and self._queue:
            self._queue_index = max(self._queue_index, 0)
            self._set_current_track(self._queue[self._queue_index])
        if self._current_track is None:
            return
        self._is_playing = not self._is_playing
        self._emit("playback_changed")

    def pause(self) -> None:
        if not self._is_playing:
            return
        self._is_playing = False
        self._emit("playback_changed")

    def play(self) -> None:
        if self._current_track is None and self._queue:
            self._queue_index = max(self._queue_index, 0)
            self._set_current_track(self._queue[self._queue_index])
        if self._current_track is None:
            return
        if self._is_playing:
            return
        self._is_playing = True
        self._emit("playback_changed")

    def skip_next(self) -> None:
        if not self._queue:
            return
        if self._queue_index + 1 >= len(self._queue):
            self._is_playing = False
            self._emit("playback_changed")
            return
        self._queue_index += 1
        self._set_current_track(self._queue[self._queue_index])
        self._is_playing = True
        self._emit("playback_changed", "queue_changed")

    def skip_previous(self) -> None:
        if not self._queue:
            return
        if self._queue_index > 0:
            self._queue_index -= 1
            self._set_current_track(self._queue[self._queue_index])
            self._is_playing = True
            self._emit("playback_changed", "queue_changed")

    def set_volume(self, level: float) -> None:
        self._volume = max(0.0, min(1.0, level))
        self._emit("volume_changed")

    def adjust_volume(self, delta: float) -> None:
        self.set_volume(self._volume + delta)

    def subscribe(self, callback: EventCallback) -> Unsubscribe:
        self._listeners.append(callback)

        def unsubscribe() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return unsubscribe

    def _set_current_track(self, track: Track) -> None:
        self._current_track = track
        self._quality_hint = demo_library.demo_quality_hint_for_track(track.id)

    def _emit(self, *events: str) -> None:
        for event in events:
            for listener in list(self._listeners):
                listener(event)
