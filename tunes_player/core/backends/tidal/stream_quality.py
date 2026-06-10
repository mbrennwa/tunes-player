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


def _policy_tidal_rank_bounds(
    policy: object,
) -> tuple[int, int]:
    from tunes_player.core.release_quality import (
        ALL_QUALITY_TIERS,
        QUALITY_FILTER_CD,
        QUALITY_FILTER_COMPRESSED,
        QUALITY_FILTER_HI_RES,
        PlaybackQualityPolicy,
        max_quality_tier,
        min_quality_tier,
    )

    if not isinstance(policy, PlaybackQualityPolicy):
        return _QUALITY_RANK["HIGH"], _QUALITY_RANK["HI_RES_LOSSLESS"]

    allowed = policy.allowed_tiers or ALL_QUALITY_TIERS
    tier_rank = {
        QUALITY_FILTER_COMPRESSED: 0,
        QUALITY_FILTER_CD: 1,
        QUALITY_FILTER_HI_RES: 2,
    }
    min_tier_api = {
        QUALITY_FILTER_COMPRESSED: _QUALITY_RANK["HIGH"],
        QUALITY_FILTER_CD: _QUALITY_RANK["LOSSLESS"],
        QUALITY_FILTER_HI_RES: _QUALITY_RANK["HI_RES"],
    }
    max_tier_api = {
        QUALITY_FILTER_COMPRESSED: _QUALITY_RANK["HIGH"],
        QUALITY_FILTER_CD: _QUALITY_RANK["LOSSLESS"],
        QUALITY_FILTER_HI_RES: _QUALITY_RANK["HI_RES_LOSSLESS"],
    }

    if policy.target_tier is None:
        min_tier = min_quality_tier(*allowed)
        max_tier = max_quality_tier(*allowed)
    else:
        min_tier = policy.target_tier
        max_tier = policy.target_tier

    return min_tier_api[min_tier], max_tier_api[max_tier]


def cap_session_quality_for_policy(
    session_quality: str,
    policy: object,
) -> str:
    """Lower session quality when playback policy caps below subscription."""
    _min_rank, max_rank = _policy_tidal_rank_bounds(policy)
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
    policy: object,
) -> list[str]:
    """Ordered audioquality values to try for one track.

    Catalog ``audioQuality`` is often ``HIGH`` even when FLAC exists; for any
    lossless-capable session we always attempt ``LOSSLESS`` before ``HIGH``.
    When hi-res is in the playback policy, try hi-res tiers even if track
    metadata only reports CD quality.
    """
    from tunes_player.core.release_quality import (
        QUALITY_FILTER_HI_RES,
        PlaybackQualityPolicy,
    )

    session_rank = _QUALITY_RANK.get(normalize_api_quality(session_quality), 0)
    min_rank, max_rank = _policy_tidal_rank_bounds(policy)

    try_hi_res = False
    if isinstance(policy, PlaybackQualityPolicy):
        if policy.target_tier == QUALITY_FILTER_HI_RES:
            try_hi_res = session_rank >= _QUALITY_RANK["HI_RES_LOSSLESS"]
        elif policy.target_tier is None:
            try_hi_res = (
                session_rank >= _QUALITY_RANK["HI_RES_LOSSLESS"]
                and track_peak_quality(track) >= _QUALITY_RANK["HI_RES"]
            )

    candidates: list[str] = []
    if try_hi_res or (
        session_rank >= _QUALITY_RANK["HI_RES_LOSSLESS"]
        and track_peak_quality(track) >= _QUALITY_RANK["HI_RES"]
    ):
        candidates.append("HI_RES_LOSSLESS")
    if try_hi_res and session_rank >= _QUALITY_RANK["HI_RES"]:
        candidates.append("HI_RES")
    if session_rank >= _QUALITY_RANK["LOSSLESS"]:
        candidates.append("LOSSLESS")
    if (
        not try_hi_res
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
    policy: object,
) -> bool:
    """Retry when a hi-res request returned CD/lossless instead of hi-res."""
    from tunes_player.core.release_quality import (
        QUALITY_FILTER_HI_RES,
        PlaybackQualityPolicy,
    )

    if not isinstance(policy, PlaybackQualityPolicy):
        return False
    if QUALITY_FILTER_HI_RES not in policy.allowed_tiers:
        return False
    if payload_is_hi_res_stream(payload):
        return False
    if tried in ("HI_RES_LOSSLESS", "HI_RES") and remaining:
        return True
    return False


def negotiate_stream_payload(
    candidates: list[str],
    request: Callable[[str], dict[str, Any]],
    *,
    policy: object | None = None,
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
            policy=policy,
        ):
            continue
        if payload_audio_quality(payload) != _LOSSY_API_TIER or quality == _LOSSY_API_TIER:
            return payload, quality
    if last_payload is None:
        raise RuntimeError("no TIDAL stream quality candidates")
    return last_payload, last_quality
