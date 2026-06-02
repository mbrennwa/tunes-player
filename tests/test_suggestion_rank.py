"""Tests for suggestion merge ranking."""

from __future__ import annotations

import unittest

from tunes_player.core.home import suggestion_added_ns
from tunes_player.core.models import Source


class TestSuggestionAddedNs(unittest.TestCase):
    def test_local_before_streaming(self) -> None:
        local = suggestion_added_ns(Source.LOCAL, index=0)
        tidal = suggestion_added_ns(Source.TIDAL, index=0)
        qobuz = suggestion_added_ns(Source.QOBUZ, index=0)
        self.assertGreater(local, tidal)
        self.assertGreater(local, qobuz)

    def test_streaming_order_deezer_qobuz_tidal(self) -> None:
        deezer = suggestion_added_ns(Source.DEEZER, index=0)
        qobuz = suggestion_added_ns(Source.QOBUZ, index=0)
        tidal = suggestion_added_ns(Source.TIDAL, index=0)
        self.assertGreater(deezer, qobuz)
        self.assertGreater(qobuz, tidal)

    def test_local_recency_within_group(self) -> None:
        newer = suggestion_added_ns(Source.LOCAL, played_at_ns=200)
        older = suggestion_added_ns(Source.LOCAL, played_at_ns=100)
        self.assertGreater(newer, older)

    def test_catalog_index_within_source(self) -> None:
        first = suggestion_added_ns(Source.QOBUZ, index=0)
        later = suggestion_added_ns(Source.QOBUZ, index=5)
        self.assertGreater(first, later)


if __name__ == "__main__":
    unittest.main()
