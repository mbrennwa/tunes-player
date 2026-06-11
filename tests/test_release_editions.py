"""Tests for UPC-based streaming edition collapse."""

from __future__ import annotations

import unittest

from tunes_player.core.models import Release, Source
from tunes_player.core.release_editions import (
    collapse_releases_by_upc,
    log_grid_release_upcs,
    normalize_upc,
    resolve_edition_release_id,
)
from tunes_player.core.release_quality import (
    QUALITY_FILTER_CD,
    QUALITY_FILTER_HI_RES,
    PlaybackPreference,
)


def _streaming_release(
    release_id: str,
    *,
    upc: str | None = None,
    peak_quality_tier: str = QUALITY_FILTER_CD,
    peak_sample_rate_hz: int | None = 44_100,
    available_quality_tiers: frozenset[str] | None = None,
    edition_release_ids: frozenset[str] = frozenset(),
) -> Release:
    tiers = available_quality_tiers or frozenset({peak_quality_tier})
    return Release(
        id=release_id,
        title="Sessions from the 17th Ward",
        artist_name="Amber Rubarth",
        source=Source.QOBUZ,
        peak_quality_tier=peak_quality_tier,
        available_quality_tiers=tiers,
        catalog_quality_ready=True,
        upc=upc,
        edition_release_ids=edition_release_ids,
        peak_sample_rate_hz=peak_sample_rate_hz,
    )


class TestNormalizeUpc(unittest.TestCase):
    def test_digits_only(self) -> None:
        self.assertEqual(normalize_upc("0060254735180"), "60254735180")

    def test_strips_formatting(self) -> None:
        self.assertEqual(normalize_upc("0-06025-47351-80"), "60254735180")

    def test_integer_upc_matches_padded_string(self) -> None:
        self.assertEqual(normalize_upc(60254735180), "60254735180")
        self.assertEqual(normalize_upc("0060254735180"), normalize_upc(60254735180))

    def test_too_short_returns_none(self) -> None:
        self.assertIsNone(normalize_upc("1234567"))
        self.assertIsNone(normalize_upc(""))
        self.assertIsNone(normalize_upc(None))


class TestCollapseReleasesByUpc(unittest.TestCase):
    def test_merges_leading_zero_upc_variants(self) -> None:
        padded = _streaming_release(
            "qobuz:album:cd",
            upc="0060254735180",
            peak_quality_tier=QUALITY_FILTER_CD,
        )
        bare = _streaming_release(
            "qobuz:album:hires",
            upc="60254735180",
            peak_quality_tier=QUALITY_FILTER_HI_RES,
            peak_sample_rate_hz=192_000,
            available_quality_tiers=frozenset({QUALITY_FILTER_HI_RES}),
        )
        self.assertEqual(len(collapse_releases_by_upc([padded, bare])), 1)

    def test_merges_same_upc_same_source(self) -> None:
        cd = _streaming_release(
            "qobuz:album:cd",
            upc="0060254735180",
            peak_quality_tier=QUALITY_FILTER_CD,
            peak_sample_rate_hz=44_100,
        )
        hires = _streaming_release(
            "qobuz:album:hires",
            upc="0060254735180",
            peak_quality_tier=QUALITY_FILTER_HI_RES,
            peak_sample_rate_hz=96_000,
            available_quality_tiers=frozenset({QUALITY_FILTER_HI_RES}),
        )
        collapsed = collapse_releases_by_upc([cd, hires])
        self.assertEqual(len(collapsed), 1)
        merged = collapsed[0]
        self.assertEqual(merged.id, "qobuz:album:hires")
        self.assertEqual(
            merged.edition_release_ids,
            frozenset({"qobuz:album:cd", "qobuz:album:hires"}),
        )
        self.assertEqual(
            merged.available_quality_tiers,
            frozenset({QUALITY_FILTER_CD, QUALITY_FILTER_HI_RES}),
        )

    def test_different_upc_not_merged(self) -> None:
        a = _streaming_release("qobuz:album:1", upc="1111111111111")
        b = _streaming_release("qobuz:album:2", upc="2222222222222")
        self.assertEqual(len(collapse_releases_by_upc([a, b])), 2)

    def test_missing_upc_not_merged(self) -> None:
        a = _streaming_release("qobuz:album:1", upc="1111111111111")
        b = _streaming_release("qobuz:album:2", upc=None)
        self.assertEqual(len(collapse_releases_by_upc([a, b])), 2)

    def test_local_releases_unchanged(self) -> None:
        local = Release(
            id="local:1",
            title="Local",
            artist_name="Artist",
            source=Source.LOCAL,
        )
        streaming = _streaming_release("qobuz:album:1", upc="1111111111111")
        collapsed = collapse_releases_by_upc([local, streaming])
        self.assertEqual(len(collapsed), 2)
        self.assertEqual(collapsed[0].id, "local:1")


class TestResolveEditionReleaseId(unittest.TestCase):
    def test_cd_preference_picks_cd_edition(self) -> None:
        cd = _streaming_release(
            "qobuz:album:cd",
            upc="0060254735180",
            peak_quality_tier=QUALITY_FILTER_CD,
            peak_sample_rate_hz=44_100,
        )
        hires = _streaming_release(
            "qobuz:album:hires",
            upc="0060254735180",
            peak_quality_tier=QUALITY_FILTER_HI_RES,
            peak_sample_rate_hz=192_000,
            available_quality_tiers=frozenset({QUALITY_FILTER_HI_RES}),
        )
        canonical = collapse_releases_by_upc([cd, hires])[0]
        summaries = {cd.id: cd, hires.id: hires, canonical.id: canonical}
        resolved = resolve_edition_release_id(
            canonical,
            preference=PlaybackPreference(max_tier=QUALITY_FILTER_CD),
            summaries=summaries,
        )
        self.assertEqual(resolved, "qobuz:album:cd")

    def test_hi_res_preference_picks_highest_sample_rate(self) -> None:
        mid = _streaming_release(
            "qobuz:album:96",
            upc="0060254735180",
            peak_quality_tier=QUALITY_FILTER_HI_RES,
            peak_sample_rate_hz=69_000,
            available_quality_tiers=frozenset({QUALITY_FILTER_HI_RES}),
        )
        top = _streaming_release(
            "qobuz:album:192",
            upc="0060254735180",
            peak_quality_tier=QUALITY_FILTER_HI_RES,
            peak_sample_rate_hz=192_000,
            available_quality_tiers=frozenset({QUALITY_FILTER_HI_RES}),
        )
        canonical = collapse_releases_by_upc([mid, top])[0]
        summaries = {mid.id: mid, top.id: top, canonical.id: canonical}
        resolved = resolve_edition_release_id(
            canonical,
            preference=PlaybackPreference(max_tier=QUALITY_FILTER_HI_RES),
            summaries=summaries,
        )
        self.assertEqual(resolved, "qobuz:album:192")

    def test_singleton_returns_self(self) -> None:
        release = _streaming_release("qobuz:album:1", upc="1111111111111")
        resolved = resolve_edition_release_id(
            release,
            preference=PlaybackPreference(max_tier=QUALITY_FILTER_HI_RES),
            summaries={release.id: release},
        )
        self.assertEqual(resolved, "qobuz:album:1")


class TestLogGridReleaseUpcs(unittest.TestCase):
    def test_logs_upc_fields(self) -> None:
        release = _streaming_release(
            "qobuz:album:1",
            upc="60254735180",
            peak_quality_tier=QUALITY_FILTER_HI_RES,
        )
        with self.assertLogs("tunes_player.core.release_editions", level="INFO") as captured:
            log_grid_release_upcs([release])
        joined = "\n".join(captured.output)
        self.assertIn("Grid UPC: 1 tile(s)", joined)
        self.assertIn("qobuz:album:1", joined)
        self.assertIn("normalized_upc='60254735180'", joined)


if __name__ == "__main__":
    unittest.main()
