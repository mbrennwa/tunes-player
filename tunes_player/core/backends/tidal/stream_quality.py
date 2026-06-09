"""TIDAL stream quality selection and playback negotiation."""

from __future__ import annotations

import json
from typing import Any, Callable, Protocol

_LOSSLESS_API_TIERS = frozenset({"LOSSLESS", "HI_RES", "HI_RES_LOSSLESS"})
_HI_RES_API_TIERS = frozenset({"HI_RES", "HI_RES_LOSSLESS"})
_LOSSY_API_TIER = "HIGH"

_QUALITY_RANK = {
    "LOW": 0,
    "HIGH": 1,
    "LOSSLESS": 2,
    "HI_RES": 3,
    "HI_RES_LOSSLESS": 4,
}


class _TrackQualityInfo(Protocol):
    audio_quality: str | None
    media_metadata_tags: object | None


def normalize_api_quality(quality: str | None) -> str:
    if not quality:
        return ""
    key = str(quality).strip().upper()
    if "." in key:
        key = key.split(".")[-1]
    return key.replace("AUDIOQUALITY.", "")


def quality_request_value(quality: object) -> str:
    """API audioquality query value (string)."""
    if hasattr(quality, "value"):
        return str(getattr(quality, "value"))
    return str(quality)


def subscription_allows_hi_res(payload: dict[str, Any]) -> bool:
    """Best-effort read of users/{id}/subscription JSON."""
    for key in (
        "hiRes",
        "hires",
        "canStreamHiRes",
        "allowsHiRes",
        "isHiRes",
        "hasHiRes",
    ):
        if payload.get(key) is True:
            return True
    highest = normalize_api_quality(
        str(payload.get("highestSoundQuality") or payload.get("soundQuality") or "")
    )
    if highest in _HI_RES_API_TIERS:
        return True
    tier = str(payload.get("tier") or payload.get("type") or "").upper()
    if "HIRES" in tier or "HI_RES" in tier or "HIFI_PLUS" in tier:
        return True
    offering = payload.get("offering") or payload.get("subscription")
    if isinstance(offering, dict) and offering and offering is not payload:
        return subscription_allows_hi_res(offering)
    blob = json.dumps(payload, default=str).upper()
    if "HI_RES_LOSSLESS" in blob or "HIRES_LOSSLESS" in blob:
        return True
    if "HIFI_PLUS" in blob or "HIFI PLUS" in blob:
        return True
    return False


def track_peak_quality(track: _TrackQualityInfo) -> int:
    peak = _QUALITY_RANK.get(normalize_api_quality(track.audio_quality), 0)
    tags = track.media_metadata_tags
    if tags is None:
        return peak
    try:
        tag_values = {normalize_api_quality(str(tag)) for tag in tags}
    except TypeError:
        tag_values = set()
    if "HIRES_LOSSLESS" in tag_values or "HI_RES_LOSSLESS" in tag_values:
        peak = max(peak, _QUALITY_RANK["HI_RES_LOSSLESS"])
    if "LOSSLESS" in tag_values:
        peak = max(peak, _QUALITY_RANK["LOSSLESS"])
    return peak


def track_supports_lossless(track: _TrackQualityInfo) -> bool:
    return track_peak_quality(track) >= _QUALITY_RANK["LOSSLESS"]


def session_quality_for_subscription(*, hi_res_entitled: bool) -> str:
    if hi_res_entitled:
        return "HI_RES_LOSSLESS"
    return "LOSSLESS"


def _ceiling_max_tidal_rank(ceiling_tier: str | None) -> int | None:
    from tunes_player.core.release_quality import (
        QUALITY_FILTER_CD,
        QUALITY_FILTER_COMPRESSED,
        QUALITY_FILTER_HI_RES,
    )

    if ceiling_tier is None or ceiling_tier == QUALITY_FILTER_HI_RES:
        return None
    if ceiling_tier == QUALITY_FILTER_CD:
        return _QUALITY_RANK["LOSSLESS"]
    if ceiling_tier == QUALITY_FILTER_COMPRESSED:
        return _QUALITY_RANK["HIGH"]
    return None


def cap_session_quality_for_ceiling(
    session_quality: str,
    ceiling_tier: str | None,
) -> str:
    """Lower session quality when the shell filter caps playback below subscription."""
    max_rank = _ceiling_max_tidal_rank(ceiling_tier)
    if max_rank is None:
        return session_quality
    session_rank = _QUALITY_RANK.get(normalize_api_quality(session_quality), 0)
    if session_rank <= max_rank:
        return normalize_api_quality(session_quality)
    for tier in ("LOSSLESS", "HIGH", "LOW"):
        if _QUALITY_RANK[tier] <= max_rank:
            return tier
    return "HIGH"


def apply_playback_quality_ceiling(
    candidates: list[str],
    ceiling_tier: str | None,
) -> list[str]:
    """Drop stream tiers above the shell playback ceiling."""
    max_rank = _ceiling_max_tidal_rank(ceiling_tier)
    if max_rank is None:
        return candidates
    capped = [item for item in candidates if _QUALITY_RANK.get(item, 0) <= max_rank]
    return capped or candidates[:1]


def playback_quality_candidates(
    session_quality: str,
    track: _TrackQualityInfo,
    *,
    ceiling_tier: str | None = None,
) -> list[str]:
    """Ordered audioquality values to try for one track.

    Catalog ``audioQuality`` is often ``HIGH`` even when FLAC exists; for any
    lossless-capable session we always attempt ``LOSSLESS`` before ``HIGH``.
    """
    session_rank = _QUALITY_RANK.get(normalize_api_quality(session_quality), 0)
    peak_rank = track_peak_quality(track)

    candidates: list[str] = []
    if (
        session_rank >= _QUALITY_RANK["HI_RES_LOSSLESS"]
        and peak_rank >= _QUALITY_RANK["HI_RES"]
    ):
        candidates.append("HI_RES_LOSSLESS")
    if session_rank >= _QUALITY_RANK["LOSSLESS"]:
        candidates.append("LOSSLESS")
    if (
        session_rank >= _QUALITY_RANK["HI_RES"]
        and "HI_RES" not in candidates
        and "HI_RES_LOSSLESS" not in candidates
    ):
        candidates.append("HI_RES")
    candidates.append(_LOSSY_API_TIER)

    seen: set[str] = set()
    ordered: list[str] = []
    for item in candidates:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return apply_playback_quality_ceiling(ordered, ceiling_tier)


def payload_audio_quality(payload: dict[str, Any]) -> str:
    return normalize_api_quality(payload.get("audioQuality"))


def should_retry_after_high(
    payload: dict[str, Any],
    *,
    tried: str,
    remaining: list[str],
) -> bool:
    if payload_audio_quality(payload) != _LOSSY_API_TIER:
        return False
    if tried == "LOSSLESS":
        return False
    return "LOSSLESS" in remaining


def negotiate_stream_payload(
    candidates: list[str],
    request: Callable[[str], dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    """Try quality tiers; downgrade when API returns HIGH for a lossless request."""
    last_payload: dict[str, Any] | None = None
    last_quality = candidates[-1] if candidates else _LOSSY_API_TIER
    for index, quality in enumerate(candidates):
        payload = request(quality)
        last_payload = payload
        last_quality = quality
        remaining = candidates[index + 1 :]
        if should_retry_after_high(payload, tried=quality, remaining=remaining):
            continue
        if payload_audio_quality(payload) != _LOSSY_API_TIER or quality == _LOSSY_API_TIER:
            return payload, quality
    if last_payload is None:
        raise RuntimeError("no TIDAL stream quality candidates")
    return last_payload, last_quality
