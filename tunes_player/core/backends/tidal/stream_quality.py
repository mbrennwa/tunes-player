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

def session_quality_for_subscription(*, hi_res_entitled: bool) -> str:
    if hi_res_entitled:
        return "HI_RES_LOSSLESS"
    return "LOSSLESS"

def _preference_tidal_rank_bounds(
    preference: object,
) -> tuple[int, int]:
    from tunes_player.core.release_quality import (
        QUALITY_FILTER_CD,
        QUALITY_FILTER_COMPRESSED,
        QUALITY_FILTER_HI_RES,
        PlaybackPreference,
    )

    if not isinstance(preference, PlaybackPreference):
        return _QUALITY_RANK["HIGH"], _QUALITY_RANK["HI_RES_LOSSLESS"]

    max_tier = preference.max_tier
    max_tier_api = {
        QUALITY_FILTER_COMPRESSED: _QUALITY_RANK["HIGH"],
        QUALITY_FILTER_CD: _QUALITY_RANK["LOSSLESS"],
        QUALITY_FILTER_HI_RES: _QUALITY_RANK["HI_RES_LOSSLESS"],
    }
    return _QUALITY_RANK["HIGH"], max_tier_api[max_tier]

def cap_session_quality_for_preference(
    session_quality: str,
    preference: object,
) -> str:
    """Lower session quality when playback preference caps below subscription."""
    _min_rank, max_rank = _preference_tidal_rank_bounds(preference)
    session_rank = _QUALITY_RANK.get(normalize_api_quality(session_quality), 0)
    if session_rank <= max_rank:
        return normalize_api_quality(session_quality)
    for tier in ("LOSSLESS", "HIGH", "LOW"):
        if _QUALITY_RANK[tier] <= max_rank:
            return tier
    return "HIGH"

def playback_quality_candidates(
    session_quality: str,
    track: _TrackQualityInfo,
    *,
    preference: object,
) -> list[str]:
    """Ordered audioquality values to try for one track."""
    from tunes_player.core.release_quality import (
        QUALITY_FILTER_HI_RES,
        PlaybackPreference,
    )

    session_rank = _QUALITY_RANK.get(normalize_api_quality(session_quality), 0)
    min_rank, max_rank = _preference_tidal_rank_bounds(preference)

    allows_hi_res = (
        isinstance(preference, PlaybackPreference)
        and preference.max_tier == QUALITY_FILTER_HI_RES
    )
    try_hi_res = (
        allows_hi_res
        and session_rank >= _QUALITY_RANK["HI_RES_LOSSLESS"]
    )

    candidates: list[str] = []
    if try_hi_res or (
        allows_hi_res
        and session_rank >= _QUALITY_RANK["HI_RES_LOSSLESS"]
        and track_peak_quality(track) >= _QUALITY_RANK["HI_RES"]
    ):
        candidates.append("HI_RES_LOSSLESS")
    if try_hi_res and session_rank >= _QUALITY_RANK["HI_RES"]:
        candidates.append("HI_RES")
    if session_rank >= _QUALITY_RANK["LOSSLESS"]:
        candidates.append("LOSSLESS")
    if (
        allows_hi_res
        and not try_hi_res
        and session_rank >= _QUALITY_RANK["HI_RES"]
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

    filtered = [
        item
        for item in ordered
        if min_rank <= _QUALITY_RANK.get(item, 0) <= max_rank
    ]
    return filtered or [_LOSSY_API_TIER]

def payload_audio_quality(payload: dict[str, Any]) -> str:
    return normalize_api_quality(payload.get("audioQuality"))

def payload_sample_rate_hz(payload: dict[str, Any]) -> int:
    rate = payload.get("sampleRate")
    if rate is None:
        return 0
    try:
        return int(rate)
    except (TypeError, ValueError):
        return 0

def payload_is_hi_res_stream(payload: dict[str, Any]) -> bool:
    """True when the negotiated stream is acoustically above CD quality."""
    from tunes_player.core.release_quality import is_acoustic_hi_res

    quality = payload_audio_quality(payload)
    if quality == _LOSSY_API_TIER:
        return False
    return is_acoustic_hi_res(payload_sample_rate_hz(payload))

def should_warn_hi_res_filter_miss(
    payload: dict[str, Any],
    track: _TrackQualityInfo,
    *,
    preference: object,
) -> bool:
    """True when catalog metadata promised hi-res but playback did not deliver it."""
    from tunes_player.core.release_quality import (
        QUALITY_FILTER_HI_RES,
        PlaybackPreference,
    )

    if not isinstance(preference, PlaybackPreference):
        return False
    if preference.max_tier != QUALITY_FILTER_HI_RES:
        return False
    if payload_is_hi_res_stream(payload):
        return False
    return track_peak_quality(track) >= _QUALITY_RANK["HI_RES"]

def should_warn_lossy_stream_fallback(
    candidates: list[str],
    resolved: str,
) -> bool:
    """True when lossless tiers were tried but playback resolved to lossy."""
    if normalize_api_quality(resolved) != _LOSSY_API_TIER:
        return False
    lossy_rank = _QUALITY_RANK[_LOSSY_API_TIER]
    return any(
        _QUALITY_RANK.get(normalize_api_quality(item), 0) > lossy_rank
        for item in candidates
    )

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

def should_retry_after_sub_hi_res(
    payload: dict[str, Any],
    *,
    tried: str,
    remaining: list[str],
    preference: object,
) -> bool:
    """Retry when a hi-res request returned CD/lossless instead of hi-res."""
    from tunes_player.core.release_quality import (
        QUALITY_FILTER_HI_RES,
        PlaybackPreference,
    )

    if not isinstance(preference, PlaybackPreference):
        return False
    if preference.max_tier != QUALITY_FILTER_HI_RES:
        return False
    if payload_is_hi_res_stream(payload):
        return False
    if tried in ("HI_RES_LOSSLESS", "HI_RES") and remaining:
        return True
    return False

def should_retry_after_lossless_cd(
    payload: dict[str, Any],
    *,
    tried: str,
    remaining: list[str],
    preference: object,
) -> bool:
    """Retry when LOSSLESS returned CD quality but hi-res tiers remain."""
    from tunes_player.core.release_quality import (
        QUALITY_FILTER_HI_RES,
        PlaybackPreference,
    )

    if not isinstance(preference, PlaybackPreference):
        return False
    if preference.max_tier != QUALITY_FILTER_HI_RES:
        return False
    if tried != "LOSSLESS":
        return False
    if payload_is_hi_res_stream(payload):
        return False
    if not remaining:
        return False
    hi_res_remaining = any(
        tier in remaining for tier in ("HI_RES_LOSSLESS", "HI_RES")
    )
    return hi_res_remaining

def negotiate_stream_payload(
    candidates: list[str],
    request: Callable[[str], dict[str, Any]],
    *,
    preference: object | None = None,
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
        if should_retry_after_sub_hi_res(
            payload,
            tried=quality,
            remaining=remaining,
            preference=preference,
        ):
            continue
        if should_retry_after_lossless_cd(
            payload,
            tried=quality,
            remaining=remaining,
            preference=preference,
        ):
            continue
        if payload_audio_quality(payload) != _LOSSY_API_TIER or quality == _LOSSY_API_TIER:
            return payload, quality
    if last_payload is None:
        raise RuntimeError("no TIDAL stream quality candidates")
    return last_payload, last_quality
