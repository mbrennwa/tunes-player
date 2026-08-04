"""Versioned JSON codec for the shared labels document."""

from __future__ import annotations

import json
from typing import Any

from tunes_player.core.labels_sync.merge import LabelEntry, LabelMap, merge_label_entries
from tunes_player.core.release_quality_tiles import parse_catalog_release_id

FORMAT_VERSION = 1
SYNC_RELATIVE_PATH = "tunes-labels.json"


def _normalize_map(label_map: LabelMap) -> LabelMap:
    """Collapse quality-tile ids onto catalog release ids."""
    result: LabelMap = {}
    for release_id, labels in label_map.items():
        catalog_id = parse_catalog_release_id(release_id)
        bucket = result.setdefault(catalog_id, {})
        for name, entry in labels.items():
            normalized_name = name.strip()
            if not normalized_name:
                continue
            merged = merge_label_entries(bucket.get(normalized_name), entry)
            if merged is not None:
                bucket[normalized_name] = merged
    return result


def dumps_label_map(label_map: LabelMap) -> bytes:
    normalized = _normalize_map(label_map)
    releases: dict[str, dict[str, dict[str, Any]]] = {}
    for release_id in sorted(normalized):
        labels_out: dict[str, dict[str, Any]] = {}
        for name, entry in sorted(
            normalized[release_id].items(),
            key=lambda item: item[0].casefold(),
        ):
            labels_out[name] = {
                "on": bool(entry.on),
                "at_ns": int(entry.at_ns),
                "by": str(entry.by or ""),
            }
        if labels_out:
            releases[release_id] = labels_out
    payload = {"format": FORMAT_VERSION, "releases": releases}
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def loads_label_map(data: bytes | str) -> LabelMap:
    if isinstance(data, bytes):
        text = data.decode("utf-8")
    else:
        text = data
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("labels document must be a JSON object")
    format_version = raw.get("format", FORMAT_VERSION)
    try:
        format_version = int(format_version)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid labels format version") from exc
    if format_version != FORMAT_VERSION:
        raise ValueError(f"unsupported labels format version: {format_version}")
    releases_raw = raw.get("releases", {})
    if not isinstance(releases_raw, dict):
        raise ValueError("releases must be an object")
    parsed: LabelMap = {}
    for release_id, labels_raw in releases_raw.items():
        if not isinstance(release_id, str) or not release_id.strip():
            continue
        if not isinstance(labels_raw, dict):
            continue
        labels: dict[str, LabelEntry] = {}
        for name, entry_raw in labels_raw.items():
            if not isinstance(name, str):
                continue
            normalized_name = name.strip()
            if not normalized_name or not isinstance(entry_raw, dict):
                continue
            on = bool(entry_raw.get("on", False))
            try:
                at_ns = int(entry_raw.get("at_ns", 0))
            except (TypeError, ValueError):
                continue
            by_raw = entry_raw.get("by", "")
            by = str(by_raw) if by_raw is not None else ""
            labels[normalized_name] = LabelEntry(on=on, at_ns=at_ns, by=by)
        if labels:
            parsed[release_id.strip()] = labels
    return _normalize_map(parsed)
