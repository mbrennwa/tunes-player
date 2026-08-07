"""Sample-rate and bit-depth extraction from provider album objects."""

from __future__ import annotations

import re
from typing import Any

from tunes_player.core.release_quality import (
    _normalize_sample_rate_hz,
    parse_qobuz_technical_spec,
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
        spec_depth, spec_rate = parse_qobuz_technical_spec(
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

def _tidal_album_needs_stream_probe(album: object) -> bool:
    from tunes_player.core.backends.tidal.stream_quality import track_peak_quality

    audio_quality = getattr(album, "audio_quality", None)
    if audio_quality is None or not str(audio_quality).strip():
        tags = getattr(album, "media_metadata_tags", None) or getattr(album, "mediaTags", None)
        if not tags:
            return False
    rank = track_peak_quality(album)
    return rank >= 2

def peak_rate_depth_from_tidal_album(album: object) -> tuple[int | None, int | None]:
    """Peak lossless rate/depth from TIDAL album metadata and serialized stream probe."""
    best_rate = 0
    best_depth: int | None = None
    for attr in ("bit_depth", "bitDepth"):
        value = getattr(album, attr, None)
        if value is not None:
            try:
                parsed_depth = int(value)
            except (TypeError, ValueError):
                parsed_depth = 0
            if parsed_depth > 0:
                best_depth = parsed_depth
    for attr in ("sample_rate", "sampling_rate", "samplingRate"):
        value = getattr(album, attr, None)
        if value is not None:
            rate_hz = _normalize_sample_rate_hz(value)
            if rate_hz > best_rate:
                best_rate = rate_hz
    _khz_re = re.compile(r"(\d+(?:\.\d+)?)\s*KHZ", re.IGNORECASE)
    for source in (
        getattr(album, "media_metadata_tags", None),
        getattr(album, "mediaTags", None),
    ):
        if not source:
            continue
        for tag in source:
            text = str(tag).upper()
            if "KHZ" not in text and "HZ" not in text:
                continue
            match = _khz_re.search(text)
            if match is None:
                continue
            rate_hz = _normalize_sample_rate_hz(float(match.group(1)))
            if rate_hz > best_rate:
                best_rate = rate_hz
    if best_rate <= 0 and _tidal_album_needs_stream_probe(album):
        from tunes_player.core.backends.tidal.catalog_stream_probe import (
            peak_rate_depth_from_tidal_stream_probe,
        )

        probed_depth, probed_rate = peak_rate_depth_from_tidal_stream_probe(album)
        if probed_rate is not None and probed_rate > 0:
            best_rate = probed_rate
            best_depth = probed_depth
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

def fetch_tidal_openapi_resource(
    session: object,
    resource: str,
    resource_id: object,
    *,
    include: str = "genres",
) -> dict | None:
    """Fetch one TIDAL OpenAPI v2 resource; returns parsed JSON or None."""
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
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None

def media_tags_from_tidal_openapi_attrs(attrs: dict) -> set[str]:
    from tunes_player.core.backends.tidal.stream_quality import normalize_api_quality

    tags: set[str] = set()
    media_tags = attrs.get("mediaTags")
    if not media_tags:
        return tags
    try:
        for tag in media_tags:
            normalized = normalize_api_quality(str(tag))
            if normalized:
                tags.add(normalized)
    except TypeError:
        pass
    return tags

def genre_from_tidal_openapi_track(session: object, track_id: object) -> str | None:
    payload = fetch_tidal_openapi_resource(session, "tracks", track_id)
    if payload is None:
        return None
    return genre_from_tidal_openapi_payload(payload)

def genre_from_tidal_openapi_payload_dict(payload: dict | None) -> str | None:
    if payload is None:
        return None
    return genre_from_tidal_openapi_payload(payload)

def media_tags_from_tidal_openapi_payload(payload: dict | None) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    data = payload.get("data")
    if not isinstance(data, dict):
        return set()
    attrs = data.get("attributes")
    if not isinstance(attrs, dict):
        return set()
    return media_tags_from_tidal_openapi_attrs(attrs)

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

def genre_from_tidal_album(
    album: object,
    *,
    fetch_tracks: bool = False,
    first_track_id: object | None = None,
    openapi_track_payload: dict | None = None,
) -> str | None:
    """Best-effort genre from TIDAL album fields or first-track OpenAPI metadata.

    TIDAL album endpoints do not expose genre; when ``fetch_tracks`` is set we read
    ``tracks/{id}?include=genres`` (one track list + one OpenAPI call at most).
    """
    for attr in ("genre", "mainGenre", "primaryGenre"):
        label = _normalize_genre_label(getattr(album, attr, None))
        if label:
            return label
    session = getattr(album, "session", None)
    if session is None:
        return None
    track_id = first_track_id
    if track_id is None and fetch_tracks:
        tracks_getter = getattr(album, "tracks", None)
        if callable(tracks_getter):
            try:
                tracks = tracks_getter(limit=1)
            except Exception:
                tracks = None
            if tracks:
                track_id = getattr(tracks[0], "id", None)
    if openapi_track_payload is not None:
        label = genre_from_tidal_openapi_payload_dict(openapi_track_payload)
        if label:
            return label
    if track_id is not None and openapi_track_payload is None:
        return genre_from_tidal_openapi_track(session, track_id)
    return None

def _session_supports_tidal_openapi(session: object) -> bool:
    config = getattr(session, "config", None)
    request_factory = getattr(session, "request", None)
    return (
        config is not None
        and getattr(config, "openapi_v2_location", None)
        and request_factory is not None
        and callable(getattr(request_factory, "request", None))
    )

def tidal_first_track_openapi_payload(
    album: object,
    *,
    fetch_tracks: bool = False,
    first_track_id: object | None = None,
) -> dict | None:
    """OpenAPI track JSON for the first album track (genre + mediaTags)."""
    session = getattr(album, "session", None)
    if session is None or not _session_supports_tidal_openapi(session):
        return None
    track_id = first_track_id
    if track_id is None and fetch_tracks:
        tracks_getter = getattr(album, "tracks", None)
        if callable(tracks_getter):
            try:
                tracks = tracks_getter(limit=1)
            except Exception:
                tracks = None
            if tracks:
                track_id = getattr(tracks[0], "id", None)
    if track_id is None:
        return None
    return fetch_tidal_openapi_resource(session, "tracks", track_id)
