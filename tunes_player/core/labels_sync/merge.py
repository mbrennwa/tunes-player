"""Per-label last-write-wins merge for the sync label map."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LabelEntry:
    on: bool
    at_ns: int
    by: str = ""


# release_id -> label_name -> entry
LabelMap = dict[str, dict[str, LabelEntry]]


def merge_label_entries(
    left: LabelEntry | None,
    right: LabelEntry | None,
) -> LabelEntry | None:
    """Keep the entry with the newest ``at_ns`` (edit time, not upload time)."""
    if left is None:
        return right
    if right is None:
        return left
    if left.at_ns > right.at_ns:
        return left
    if right.at_ns > left.at_ns:
        return right
    # Stable tie-break: tombstone (off) wins, then lexicographic ``by``.
    if left.on != right.on:
        return left if not left.on else right
    if left.by <= right.by:
        return left
    return right


def merge_label_maps(local: LabelMap, remote: LabelMap) -> LabelMap:
    """Union of both maps with per-(release, label) LWW."""
    result: LabelMap = {}
    release_ids = set(local) | set(remote)
    for release_id in release_ids:
        local_labels = local.get(release_id, {})
        remote_labels = remote.get(release_id, {})
        label_names = set(local_labels) | set(remote_labels)
        merged_labels: dict[str, LabelEntry] = {}
        for name in label_names:
            entry = merge_label_entries(
                local_labels.get(name),
                remote_labels.get(name),
            )
            if entry is not None:
                merged_labels[name] = entry
        if merged_labels:
            result[release_id] = merged_labels
    return result
