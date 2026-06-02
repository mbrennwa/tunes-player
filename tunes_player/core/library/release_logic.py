"""Release completeness and type inference for local library groups."""

from __future__ import annotations

from tunes_player.core.models import ReleaseCompleteness, ReleaseType


def infer_release_metadata(
    *,
    track_count: int,
    is_synthetic: bool,
    total_tracks_tag: int | None,
    max_track_number: int | None,
) -> tuple[ReleaseCompleteness, ReleaseType, int | None]:
    if is_synthetic:
        return ReleaseCompleteness.SYNTHETIC, ReleaseType.SYNTHETIC, 1

    expected = total_tracks_tag
    if expected is None and max_track_number is not None and max_track_number > track_count:
        expected = max_track_number

    if expected is not None and track_count < expected:
        return ReleaseCompleteness.PARTIAL, ReleaseType.ALBUM, expected

    if track_count == 1:
        return ReleaseCompleteness.COMPLETE, ReleaseType.SINGLE, expected or 1

    return ReleaseCompleteness.COMPLETE, ReleaseType.ALBUM, expected
