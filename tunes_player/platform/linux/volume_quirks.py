"""Parse bundled and user hardware-volume quirk tables."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from platformdirs import user_config_dir

from tunes_player.core.config import APP_NAME

_WILDCARD = "*"
_MATCH_LINE = re.compile(
    r"^MATCH\s+(.+?)\s=>\s+(hw|nohw)(?:\s+mixer=(\S+))?\s*$",
    re.IGNORECASE,
)
_FIELD_USB = re.compile(r"^usb=([0-9a-f]{4}:[0-9a-f]{4})$", re.IGNORECASE)
_FIELD_FW = re.compile(r"^fw=(.+)$", re.IGNORECASE)
_FIELD_DEV = re.compile(r"^dev=(\d+|\*)$", re.IGNORECASE)

_quirk_cache: list[QuirkRule] | None = None


@dataclass(frozen=True)
class CardIdentity:
    card: int
    device: int | None
    usb_id: str | None
    firmware: str | None
    long_name: str | None


@dataclass(frozen=True)
class QuirkRule:
    usb_id: str | None
    firmware: str | None
    device: int | None
    name_pattern: re.Pattern[str] | None
    hardware_volume: bool
    mixer: str | None
    user_rule: bool

    @property
    def specificity(self) -> tuple[int, int]:
        score = 0
        if self.usb_id is not None:
            score += 1
        if self.firmware is not None:
            score += 1
        if self.device is not None:
            score += 1
        if self.name_pattern is not None:
            score += 1
        return (score, 1 if self.user_rule else 0)


@dataclass(frozen=True)
class QuirkMatch:
    hardware_volume: bool
    mixer: str | None
    rule: QuirkRule


def clear_quirk_cache() -> None:
    global _quirk_cache
    _quirk_cache = None


def bundled_quirks_path() -> Path:
    return Path(
        str(
            resources.files("tunes_player.platform.linux.data").joinpath(
                "hardware_volume.quirks"
            )
        )
    )


def user_quirks_path() -> Path:
    return Path(user_config_dir(APP_NAME)) / "hardware_volume.quirks"


def load_quirk_rules() -> list[QuirkRule]:
    global _quirk_cache
    if _quirk_cache is not None:
        return _quirk_cache
    bundled = _parse_quirk_file(bundled_quirks_path(), user_rule=False)
    user_path = user_quirks_path()
    user_rules = _parse_quirk_file(user_path, user_rule=True) if user_path.is_file() else []
    merged = bundled + user_rules
    merged.sort(key=lambda rule: rule.specificity, reverse=True)
    _quirk_cache = merged
    return merged


def match_quirk(identity: CardIdentity) -> QuirkMatch | None:
    for rule in load_quirk_rules():
        if _rule_matches(rule, identity):
            return QuirkMatch(
                hardware_volume=rule.hardware_volume,
                mixer=rule.mixer,
                rule=rule,
            )
    return None


def _parse_quirk_file(path: Path, *, user_rule: bool) -> list[QuirkRule]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rules: list[QuirkRule] = []
    for line in text.splitlines():
        rule = _parse_quirk_line(line, user_rule=user_rule)
        if rule is not None:
            rules.append(rule)
    return rules


def _parse_quirk_line(line: str, *, user_rule: bool) -> QuirkRule | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    match = _MATCH_LINE.match(stripped)
    if match is None:
        return None
    fields_text, result, mixer = match.groups()
    hardware_volume = result.casefold() == "hw"
    usb_id: str | None = None
    firmware: str | None = None
    device: int | None = None
    name_pattern: re.Pattern[str] | None = None
    for token in fields_text.split():
        if _FIELD_USB.match(token):
            usb_id = token.split("=", 1)[1].casefold()
        elif _FIELD_FW.match(token):
            raw = token.split("=", 1)[1]
            firmware = None if raw == _WILDCARD else raw
        elif _FIELD_DEV.match(token):
            raw = token.split("=", 1)[1]
            device = None if raw == _WILDCARD else int(raw)
        elif token.startswith("name~"):
            pattern = token[5:]
            if pattern != _WILDCARD:
                name_pattern = re.compile(pattern, re.IGNORECASE)
        else:
            return None
    if mixer is not None and not hardware_volume:
        return None
    return QuirkRule(
        usb_id=usb_id,
        firmware=firmware,
        device=device,
        name_pattern=name_pattern,
        hardware_volume=hardware_volume,
        mixer=mixer,
        user_rule=user_rule,
    )


def _rule_matches(rule: QuirkRule, identity: CardIdentity) -> bool:
    if rule.usb_id is not None:
        if identity.usb_id is None or identity.usb_id.casefold() != rule.usb_id:
            return False
    if rule.firmware is not None:
        if identity.firmware is None or identity.firmware != rule.firmware:
            return False
    if rule.device is not None:
        if identity.device is None or identity.device != rule.device:
            return False
    if rule.name_pattern is not None:
        if identity.long_name is None or not rule.name_pattern.search(identity.long_name):
            return False
    return True
