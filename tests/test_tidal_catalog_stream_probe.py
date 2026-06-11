"""Tests for serialized TIDAL catalog stream probes."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from tunes_player.core.backends.tidal.catalog_stream_probe import (
    clear_tidal_catalog_stream_probe_cache,
    peak_rate_depth_from_tidal_stream_probe,
)


class TidalCatalogStreamProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_tidal_catalog_stream_probe_cache()

    def test_caches_probe_result_per_album(self) -> None:
        calls = {"count": 0}

        def probe() -> list[tuple[int, int]]:
            calls["count"] += 1
            return [(24, 96_000)]

        album = SimpleNamespace(id=42, get_audio_resolution=probe)
        first = peak_rate_depth_from_tidal_stream_probe(album)
        second = peak_rate_depth_from_tidal_stream_probe(album)
        self.assertEqual(first, (24, 96_000))
        self.assertEqual(second, (24, 96_000))
        self.assertEqual(calls["count"], 1)

    def test_probe_failure_does_not_cache_success(self) -> None:
        album = SimpleNamespace(
            id=99,
            get_audio_resolution=lambda: (_ for _ in ()).throw(RuntimeError("429")),
        )
        self.assertEqual(peak_rate_depth_from_tidal_stream_probe(album), (None, None))


if __name__ == "__main__":
    unittest.main()
