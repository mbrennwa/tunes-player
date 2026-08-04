"""Tests for per-label LWW merge."""

from __future__ import annotations

import unittest

from tunes_player.core.labels_sync.merge import LabelEntry, merge_label_entries, merge_label_maps


class LabelMergeTests(unittest.TestCase):
    def test_newer_at_ns_wins(self) -> None:
        older = LabelEntry(on=True, at_ns=100, by="a")
        newer = LabelEntry(on=False, at_ns=200, by="b")
        self.assertEqual(merge_label_entries(older, newer), newer)
        self.assertEqual(merge_label_entries(newer, older), newer)

    def test_offline_older_loses_to_recent_edit(self) -> None:
        home = LabelEntry(on=True, at_ns=1_000_000, by="home")
        work_old = LabelEntry(on=False, at_ns=500_000, by="work")
        self.assertEqual(merge_label_entries(home, work_old), home)

    def test_tie_break_tombstone_wins(self) -> None:
        on = LabelEntry(on=True, at_ns=100, by="a")
        off = LabelEntry(on=False, at_ns=100, by="b")
        self.assertEqual(merge_label_entries(on, off), off)

    def test_merge_maps_independent_labels(self) -> None:
        local = {
            "tidal:album:1": {
                "buy": LabelEntry(on=True, at_ns=10, by="home"),
                "vinyl": LabelEntry(on=True, at_ns=5, by="home"),
            }
        }
        remote = {
            "tidal:album:1": {
                "buy": LabelEntry(on=False, at_ns=20, by="work"),
                "vinyl": LabelEntry(on=True, at_ns=1, by="work"),
            },
            "local:album:x": {
                "later": LabelEntry(on=True, at_ns=30, by="work"),
            },
        }
        merged = merge_label_maps(local, remote)
        self.assertFalse(merged["tidal:album:1"]["buy"].on)
        self.assertTrue(merged["tidal:album:1"]["vinyl"].on)
        self.assertEqual(merged["tidal:album:1"]["vinyl"].by, "home")
        self.assertIn("local:album:x", merged)


if __name__ == "__main__":
    unittest.main()
