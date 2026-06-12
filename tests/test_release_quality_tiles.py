"""Tests for per-quality-tier grid expansion."""

from __future__ import annotations

import unittest
from dataclasses import replace

from tunes_player.core.models import Release, ReleaseCompleteness, Source
from tunes_player.core.release_quality import (
    QUALITY_FILTER_CD,
    QUALITY_FILTER_COMPRESSED,
    QUALITY_FILTER_HI_RES,
)
from tunes_player.core.release_quality import catalog_quality_label_for_release
from tunes_player.core.release_quality_tiles import (
    expand_releases_by_quality_tier,
    parse_catalog_release_id,
    parse_quality_tier_suffix,
    playback_tier_for_release_id,
    quality_tile_id,
)


def _release(
    release_id: str,
    *,
    source: Source = Source.TIDAL,
    tiers: frozenset[str] = frozenset({QUALITY_FILTER_CD}),
    catalog_release_id: str = "",
    peak_sample_rate_hz: int | None = 44_100,
    peak_bit_depth: int | None = 16,
    catalog_quality_ready: bool = True,
) -> Release:
    peak = max(tiers, key=lambda t: ("compressed", "cd", "hi_res").index(t)) if tiers else ""
    return Release(
        id=release_id,
        title="Album",
        artist_name="Artist",
        source=source,
        track_count=10,
        completeness=ReleaseCompleteness.COMPLETE,
        peak_quality_tier=peak,
        available_quality_tiers=tiers,
        catalog_quality_ready=catalog_quality_ready,
        catalog_release_id=catalog_release_id or release_id,
        peak_sample_rate_hz=peak_sample_rate_hz,
        peak_bit_depth=peak_bit_depth,
    )


class ParseTileIdTests(unittest.TestCase):
    def test_parse_catalog_release_id_strips_suffix(self) -> None:
        self.assertEqual(
            parse_catalog_release_id("tidal:album:404893856@hi_res"),
            "tidal:album:404893856",
        )

    def test_parse_catalog_release_id_unchanged_without_suffix(self) -> None:
        self.assertEqual(parse_catalog_release_id("qobuz:album:abc"), "qobuz:album:abc")

    def test_parse_quality_tier_suffix(self) -> None:
        self.assertEqual(parse_quality_tier_suffix("tidal:album:1@cd"), QUALITY_FILTER_CD)
        self.assertIsNone(parse_quality_tier_suffix("tidal:album:1"))

    def test_quality_tile_id(self) -> None:
        self.assertEqual(
            quality_tile_id("tidal:album:1", QUALITY_FILTER_HI_RES),
            "tidal:album:1@hi_res",
        )


class ExpandReleasesByQualityTierTests(unittest.TestCase):
    def test_single_tier_keeps_catalog_id(self) -> None:
        release = _release("tidal:album:1", tiers=frozenset({QUALITY_FILTER_CD}))
        expanded = expand_releases_by_quality_tier([release])
        self.assertEqual(len(expanded), 1)
        tile = expanded[0]
        self.assertEqual(tile.id, "tidal:album:1")
        self.assertEqual(tile.quality_tier, QUALITY_FILTER_CD)
        self.assertEqual(tile.available_quality_tiers, frozenset({QUALITY_FILTER_CD}))

    def test_multi_tier_splits_into_synthetic_ids(self) -> None:
        release = _release(
            "tidal:album:404893856",
            tiers=frozenset({QUALITY_FILTER_CD, QUALITY_FILTER_HI_RES}),
            peak_sample_rate_hz=96_000,
            peak_bit_depth=24,
        )
        expanded = expand_releases_by_quality_tier([release])
        self.assertEqual(len(expanded), 2)
        by_id = {tile.id: tile for tile in expanded}
        self.assertIn("tidal:album:404893856@cd", by_id)
        self.assertIn("tidal:album:404893856@hi_res", by_id)
        cd_tile = by_id["tidal:album:404893856@cd"]
        hires_tile = by_id["tidal:album:404893856@hi_res"]
        self.assertEqual(cd_tile.catalog_release_id, "tidal:album:404893856")
        self.assertEqual(hires_tile.catalog_release_id, "tidal:album:404893856")
        self.assertEqual(cd_tile.quality_tier, QUALITY_FILTER_CD)
        self.assertEqual(hires_tile.quality_tier, QUALITY_FILTER_HI_RES)
        self.assertEqual(hires_tile.peak_sample_rate_hz, 96_000)
        self.assertEqual(hires_tile.peak_bit_depth, 24)

    def test_stub_not_expanded_until_ready(self) -> None:
        release = _release(
            "qobuz:album:stub",
            tiers=frozenset(),
            catalog_quality_ready=False,
        )
        expanded = expand_releases_by_quality_tier([release])
        self.assertEqual(len(expanded), 1)
        self.assertEqual(expanded[0].id, "qobuz:album:stub")
        self.assertEqual(expanded[0].quality_tier, "")

    def test_local_multi_tier_splits(self) -> None:
        release = _release(
            "local:release:1",
            source=Source.LOCAL,
            tiers=frozenset({QUALITY_FILTER_CD, QUALITY_FILTER_HI_RES}),
        )
        expanded = expand_releases_by_quality_tier([release])
        self.assertEqual(len(expanded), 2)
        self.assertEqual(expanded[0].catalog_release_id, "local:release:1")

    def test_hi_res_tile_label_when_only_sample_rate_known(self) -> None:
        release = _release(
            "tidal:album:466103586",
            tiers=frozenset({QUALITY_FILTER_HI_RES}),
            peak_sample_rate_hz=96_000,
            peak_bit_depth=None,
        )
        tile = expand_releases_by_quality_tier([release])[0]
        self.assertEqual(tile.peak_bit_depth, 24)
        self.assertEqual(catalog_quality_label_for_release(tile), "96/24")

    def test_dual_tier_tidal_hi_res_tile_gets_peak_resolution(self) -> None:
        release = _release(
            "tidal:album:404893856",
            tiers=frozenset({QUALITY_FILTER_CD, QUALITY_FILTER_HI_RES}),
            peak_sample_rate_hz=96_000,
            peak_bit_depth=24,
        )
        expanded = expand_releases_by_quality_tier([release])
        by_id = {tile.id: tile for tile in expanded}
        self.assertEqual(
            catalog_quality_label_for_release(by_id["tidal:album:404893856@cd"]),
            "44.1/16",
        )
        self.assertEqual(
            catalog_quality_label_for_release(by_id["tidal:album:404893856@hi_res"]),
            "96/24",
        )

    def test_three_tier_album(self) -> None:
        release = _release(
            "tidal:album:multi",
            tiers=frozenset(
                {QUALITY_FILTER_COMPRESSED, QUALITY_FILTER_CD, QUALITY_FILTER_HI_RES},
            ),
        )
        expanded = expand_releases_by_quality_tier([release])
        self.assertEqual(len(expanded), 3)


class PlaybackTierForReleaseIdTests(unittest.TestCase):
    def test_suffix_drives_tier(self) -> None:
        tier = playback_tier_for_release_id(
            "tidal:album:1@hi_res",
            summaries={},
        )
        self.assertEqual(tier, QUALITY_FILTER_HI_RES)

    def test_cached_tile_tier(self) -> None:
        tile = replace(
            _release(
                "tidal:album:1@cd",
                tiers=frozenset({QUALITY_FILTER_CD}),
            ),
            quality_tier=QUALITY_FILTER_CD,
        )
        tier = playback_tier_for_release_id(
            tile.id,
            summaries={tile.id: tile},
        )
        self.assertEqual(tier, QUALITY_FILTER_CD)


if __name__ == "__main__":
    unittest.main()
