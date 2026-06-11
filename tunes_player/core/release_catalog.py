"""Sample-rate and bit-depth extraction from provider album objects."""

from __future__ import annotations

import re
from typing import Any

from tunes_player.core.release_quality import (
    _normalize_sample_rate_hz,
    _sample_rate_hz_from_tidal_audio_resolution,
    peak_sample_rate_hz_from_tidal_album,
)

_TECH_SPEC_RE = re.compile(
    r"(\d+)\s*(?:bit|-bit).*?(\d+(?:\.\d+)?)\s*khz",
    re.IGNORECASE,
)


def _parse_technical_spec(spec: object) -> tuple[int | None, int | None]:
    if not isinstance(spec, str) or not spec.strip():
        return None, None
    match = _TECH_SPEC_RE.search(spec)
    if match is None:
        return None, None
    try:
        depth = int(match.group(1))
    except (TypeError, ValueError):
        depth = None
    rate_hz = _normalize_sample_rate_hz(float(match.group(2)))
    return (
        depth if depth and depth > 0 else None,
        rate_hz if rate_hz > 0 else None,
    )


def _best_qobuz_rate_depth(
    *,
    bit_depth: int | None,
    sample_rate_hz: int | None,
) -> tuple[int | None, int | None]:
    depth = bit_depth if bit_depth and bit_depth > 0 else None
    rate_hz = sample_rate_hz if sample_rate_hz and sample_rate_hz > 0 else None
    if depth is not None and rate_hz is not None:
        return depth, rate_hz
    return depth, rate_hz


def _qobuz_peak_rate_depth_from_dict(item: dict[str, Any]) -> tuple[int | None, int | None]:
    depth = None
    rate_hz = None
    raw_depth = item.get("maximum_bit_depth")
    if raw_depth is not None:
        try:
            parsed_depth = int(raw_depth)
            if parsed_depth > 0:
                depth = parsed_depth
        except (TypeError, ValueError):
            pass
    parsed_rate = _normalize_sample_rate_hz(item.get("maximum_sampling_rate"))
    if parsed_rate > 0:
        rate_hz = parsed_rate
    if depth is None or rate_hz is None:
        spec_depth, spec_rate = _parse_technical_spec(
            item.get("maximum_technical_specifications"),
        )
        if depth is None:
            depth = spec_depth
        if rate_hz is None:
            rate_hz = spec_rate
    return _best_qobuz_rate_depth(bit_depth=depth, sample_rate_hz=rate_hz)


def peak_rate_depth_from_qobuz_album(album: dict[str, Any]) -> tuple[int | None, int | None]:
    """Best peak rate/depth from Qobuz album/get JSON (album + track items)."""
    best_depth: int | None = None
    best_rate = 0
    candidates = [album]
    tracks = album.get("tracks")
    if isinstance(tracks, dict):
        for item in tracks.get("items") or []:
            if isinstance(item, dict):
                candidates.append(item)
    for item in candidates:
        depth, rate_hz = _qobuz_peak_rate_depth_from_dict(item)
        if rate_hz is not None and rate_hz > best_rate:
            best_rate = rate_hz
            best_depth = depth
    if best_rate > 0:
        return best_depth, best_rate
    return None, None


def peak_sample_rate_from_qobuz_album(album: dict[str, Any]) -> int | None:
    _, rate_hz = peak_rate_depth_from_qobuz_album(album)
    return rate_hz


def peak_bit_depth_from_qobuz_album(album: dict[str, Any]) -> int | None:
    depth, rate_hz = peak_rate_depth_from_qobuz_album(album)
    if depth is not None:
        return depth
    if rate_hz and rate_hz > 0:
        return 24 if rate_hz > 48_000 else 16
    return None


def peak_rate_depth_from_tidal_album(album: object) -> tuple[int | None, int | None]:
    """Peak lossless rate/depth from TIDAL album metadata and audio resolution."""
    best_rate = 0
    best_depth: int | None = None
    getter = getattr(album, "get_audio_resolution", None)
    if callable(getter):
        try:
            resolutions = getter()
        except Exception:
            resolutions = None
        for item in resolutions or []:
            try:
                depth = int(item[0])
                rate_hz = int(item[1])
            except (IndexError, TypeError, ValueError):
                continue
            if rate_hz > best_rate:
                best_rate = rate_hz
                best_depth = depth if depth > 0 else best_depth
    for attr in ("sample_rate", "sampling_rate", "samplingRate"):
        value = getattr(album, attr, None)
        if value is not None:
            rate_hz = _normalize_sample_rate_hz(value)
            if rate_hz > best_rate:
                best_rate = rate_hz
    resolution_rate = _sample_rate_hz_from_tidal_audio_resolution(album)
    if resolution_rate > best_rate:
        best_rate = resolution_rate
    if best_rate <= 0:
        legacy = peak_sample_rate_hz_from_tidal_album(album)
        if legacy and legacy > 0:
            best_rate = legacy
    if best_rate <= 0:
        return None, None
    if best_depth is None or best_depth <= 0:
        best_depth = 24 if best_rate > 48_000 else 16
    return best_depth, best_rate


def peak_sample_rate_from_tidal_album(album: object) -> int | None:
    _, rate_hz = peak_rate_depth_from_tidal_album(album)
    return rate_hz


def peak_bit_depth_from_tidal_album(album: object) -> int | None:
    depth, _ = peak_rate_depth_from_tidal_album(album)
    return depth


def _normalize_genre_label(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if "://" in text and "/genre/" in text.casefold():
        segment = text.rstrip("/").rsplit("/", 1)[-1]
        if segment and segment.casefold() != "genre":
            slug = segment.replace("-", " ").replace("_", " ")
            return slug.title()
    return text


def _genre_from_tidal_openapi_genre_attrs(attrs: object) -> str | None:
    if not isinstance(attrs, dict):
        return None
    for key in ("genreName", "name", "title"):
        label = _normalize_genre_label(attrs.get(key))
        if label:
            return label
    return None


def genre_from_tidal_openapi_payload(payload: object) -> str | None:
    """Parse genre from TIDAL OpenAPI v2 JSON:API (album or track) responses."""
    if not isinstance(payload, dict):
        return None
    for item in payload.get("included") or []:
        if not isinstance(item, dict) or item.get("type") != "genres":
            continue
        label = _genre_from_tidal_openapi_genre_attrs(item.get("attributes"))
        if label:
            return label
    data = payload.get("data")
    if isinstance(data, dict):
        relationships = data.get("relationships")
        if isinstance(relationships, dict):
            genres_rel = relationships.get("genres")
            if isinstance(genres_rel, dict):
                rel_data = genres_rel.get("data")
                if isinstance(rel_data, list) and rel_data:
                    genre_id = rel_data[0].get("id") if isinstance(rel_data[0], dict) else None
                    if genre_id is not None:
                        for item in payload.get("included") or []:
                            if (
                                isinstance(item, dict)
                                and item.get("type") == "genres"
                                and str(item.get("id")) == str(genre_id)
                            ):
                                label = _genre_from_tidal_openapi_genre_attrs(item.get("attributes"))
                                if label:
                                    return label
    return None


def _genre_from_tidal_openapi_resource(
    session: object,
    resource: str,
    resource_id: object,
    *,
    include: str = "genres",
) -> str | None:
    request_factory = getattr(session, "request", None)
    config = getattr(session, "config", None)
    openapi_base = getattr(config, "openapi_v2_location", None) if config is not None else None
    if request_factory is None or openapi_base is None or resource_id is None:
        return None
    try:
        response = request_factory.request(
            "GET",
            f"{resource}/{resource_id}",
            params={"include": include},
            base_url=openapi_base,
        )
        if not getattr(response, "ok", False):
            return None
        return genre_from_tidal_openapi_payload(response.json())
    except Exception:
        return None


def genre_from_tidal_openapi_album(session: object, album_id: object) -> str | None:
    return _genre_from_tidal_openapi_resource(session, "albums", album_id)


def genre_from_tidal_openapi_track(session: object, track_id: object) -> str | None:
    return _genre_from_tidal_openapi_resource(session, "tracks", track_id)


def genre_from_tidal_album_json(data: object) -> str | None:
    if not isinstance(data, dict):
        return None
    for key in ("genre", "mainGenre", "primaryGenre", "genreName"):
        label = _normalize_genre_label(data.get(key))
        if label:
            return label
    genres = data.get("genres")
    if isinstance(genres, list):
        for item in genres:
            if isinstance(item, str):
                label = _normalize_genre_label(item)
                if label:
                    return label
            if isinstance(item, dict):
                for key in ("genreName", "name", "title", "path"):
                    label = _normalize_genre_label(item.get(key))
                    if label:
                        return label
    return None


def genre_from_tidal_album(album: object, *, fetch_tracks: bool = False) -> str | None:
    """Best-effort genre label from TIDAL album or first-track OpenAPI metadata."""
    album_id = getattr(album, "id", None)
    session = getattr(album, "session", None)
    if album_id is not None and session is not None:
        label = genre_from_tidal_openapi_album(session, album_id)
        if label:
            return label
    for attr in ("genre", "mainGenre", "primaryGenre"):
        label = _normalize_genre_label(getattr(album, attr, None))
        if label:
            return label
    request_factory = getattr(session, "request", None) if session is not None else None
    if album_id is not None and request_factory is not None:
        try:
            response = request_factory.request("GET", f"albums/{album_id}")
            if getattr(response, "ok", False):
                label = genre_from_tidal_album_json(response.json())
                if label:
                    return label
        except Exception:
            pass
    if not fetch_tracks:
        return None
    tracks_getter = getattr(album, "tracks", None)
    if callable(tracks_getter) and session is not None:
        try:
            tracks = tracks_getter(limit=1)
        except Exception:
            tracks = None
        if tracks:
            track_id = getattr(tracks[0], "id", None)
            if track_id is not None:
                label = genre_from_tidal_openapi_track(session, track_id)
                if label:
                    return label
    return None
