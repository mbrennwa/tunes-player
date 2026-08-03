"""Tests for hardware volume quirk table parsing and matching."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tunes_player.platform.linux.volume_quirks import (
    CardIdentity,
    clear_quirk_cache,
    match_quirk,
)


class VolumeQuirksTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_quirk_cache()

    def tearDown(self) -> None:
        clear_quirk_cache()

    def test_parse_comments_and_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hardware_volume.quirks"
            path.write_text(
                """
                # comment
                MATCH usb=abcd:1234 => nohw

                MATCH usb=1111:2222 fw=1.00 => hw mixer=PCM
                """,
                encoding="utf-8",
            )
            parsed = _load_file(path)
            self.assertEqual(len(parsed), 2)
            self.assertFalse(parsed[0].hardware_volume)
            self.assertTrue(parsed[1].hardware_volume)
            self.assertEqual(parsed[1].mixer, "PCM")

    def test_match_specificity_prefers_firmware(self) -> None:
        rules = _load_text(
            """
            MATCH usb=2188:6537 => hw mixer=PCM
            MATCH usb=2188:6537 fw=3144 => nohw
            """
        )
        identity = CardIdentity(
            card=1,
            device=0,
            usb_id="2188:6537",
            firmware="3144",
            long_name="Holo May",
        )
        matched = _match_rules(rules, identity)
        self.assertIsNotNone(matched)
        assert matched is not None
        self.assertFalse(matched.hardware_volume)

    def test_user_rule_wins_on_equal_specificity(self) -> None:
        bundled = _load_text("MATCH usb=aaaa:bbbb => hw mixer=PCM", user_rule=False)
        user = _load_text("MATCH usb=aaaa:bbbb => nohw", user_rule=True)
        rules = bundled + user
        rules.sort(key=lambda rule: rule.specificity, reverse=True)
        identity = CardIdentity(
            card=0,
            device=0,
            usb_id="aaaa:bbbb",
            firmware=None,
            long_name=None,
        )
        matched = _match_rules(rules, identity)
        self.assertIsNotNone(matched)
        assert matched is not None
        self.assertFalse(matched.hardware_volume)

    def test_name_regex_match(self) -> None:
        with patch(
            "tunes_player.platform.linux.volume_quirks.load_quirk_rules",
            return_value=_load_text("MATCH name~May => nohw"),
        ):
            clear_quirk_cache()
            identity = CardIdentity(
                card=0,
                device=0,
                usb_id="9999:0001",
                firmware=None,
                long_name="Holo Audio May USB",
            )
            matched = match_quirk(identity)
            self.assertIsNotNone(matched)
            assert matched is not None
            self.assertFalse(matched.hardware_volume)

    def test_bundled_holo_fixed_firmware(self) -> None:
        clear_quirk_cache()
        identity = CardIdentity(
            card=2,
            device=0,
            usb_id="152a:87c0",
            firmware="3144",
            long_name="Holo Audio Holo Audio UAC2.0 Gen2.1 Enhanc",
        )
        matched = match_quirk(identity)
        self.assertIsNotNone(matched)
        assert matched is not None
        self.assertFalse(matched.hardware_volume)

    def test_holo_other_firmware_not_matched_by_fixed_rule(self) -> None:
        clear_quirk_cache()
        identity = CardIdentity(
            card=2,
            device=0,
            usb_id="152a:87c0",
            firmware="3014",
            long_name="Holo Audio Holo Audio UAC2.0 Gen2.1 Enhanc",
        )
        matched = match_quirk(identity)
        self.assertIsNone(matched)

    def test_malformed_lines_ignored(self) -> None:
        parsed = _load_text(
            """
            not a rule
            MATCH usb=1234:5678 => maybe
            MATCH usb=1234:5678 => hw mixer=Speaker
            """
        )
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].mixer, "Speaker")


def _load_file(path: Path):
    from tunes_player.platform.linux import volume_quirks as vq

    return vq._parse_quirk_file(path, user_rule=False)


def _load_text(text: str, *, user_rule: bool = False):
    from tunes_player.platform.linux import volume_quirks as vq

    rules = []
    for line in text.splitlines():
        rule = vq._parse_quirk_line(line, user_rule=user_rule)
        if rule is not None:
            rules.append(rule)
    return rules


def _match_rules(rules, identity):
    from tunes_player.platform.linux.volume_quirks import QuirkMatch

    ordered = sorted(rules, key=lambda rule: rule.specificity, reverse=True)
    for rule in ordered:
        from tunes_player.platform.linux import volume_quirks as vq

        if vq._rule_matches(rule, identity):
            return QuirkMatch(
                hardware_volume=rule.hardware_volume,
                mixer=rule.mixer,
                rule=rule,
            )
    return None


if __name__ == "__main__":
    unittest.main()
