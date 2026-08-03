"""Probe ALSA codec capabilities from /proc/asound."""

from __future__ import annotations

import json
import re
from pathlib import Path

from tunes_player.core.playback.output_profile import HwAudioCaps

_RATES_LINE = re.compile(r"rates\s+\[0x[0-9a-f]+\]:\s*(.+)", re.IGNORECASE)
_BITS_LINE = re.compile(r"bits\s+\[0x[0-9a-f]+\]:\s*(.+)", re.IGNORECASE)
_caps_cache: dict[int, HwAudioCaps] = {}

def _parse_rate_bit_tokens(line: str) -> tuple[int, ...]:
    values: list[int] = []
    for token in line.split():
        try:
            values.append(int(token))
        except ValueError:
            continue
    return tuple(sorted(set(values)))

def _read_codec_paths(card: int) -> list[Path]:
    codec_dir = Path(f"/proc/asound/card{card}")
    if not codec_dir.is_dir():
        return []
    return sorted(codec_dir.glob("codec#*"))

def _parse_codec_file(path: Path) -> HwAudioCaps | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    rates: set[int] = set()
    bits: set[int] = set()
    in_analog = False
    for line in text.splitlines():
        if 'type="Audio"' in line and "Analog" in line:
            in_analog = True
        if line.startswith("Node ") and in_analog:
            in_analog = "Audio Output" in line or "Analog" in line
        rate_match = _RATES_LINE.search(line)
        if rate_match and (in_analog or not rates):
            rates.update(_parse_rate_bit_tokens(rate_match.group(1)))
        bit_match = _BITS_LINE.search(line)
        if bit_match and (in_analog or not bits):
            bits.update(_parse_rate_bit_tokens(bit_match.group(1)))
    if not rates and not bits:
        for line in text.splitlines():
            rate_match = _RATES_LINE.search(line)
            if rate_match:
                rates.update(_parse_rate_bit_tokens(rate_match.group(1)))
            bit_match = _BITS_LINE.search(line)
            if bit_match:
                bits.update(_parse_rate_bit_tokens(bit_match.group(1)))
    if not rates:
        return None
    if not bits:
        bits = {16}
    return HwAudioCaps(
        sample_rates=tuple(sorted(rates)),
        bit_depths=tuple(sorted(bits)),
        max_channels=2,
    )

def probe_card_caps(card: int, *, data_dir: Path | None = None) -> HwAudioCaps | None:
    """Return hardware PCM caps for card, using cache and optional JSON persistence."""
    if card in _caps_cache:
        return _caps_cache[card]

    cache_path = None
    if data_dir is not None:
        cache_path = data_dir / f"alsa-caps-{card}.json"
        if cache_path.is_file():
            try:
                raw = json.loads(cache_path.read_text(encoding="utf-8"))
                caps = HwAudioCaps(
                    sample_rates=tuple(raw["sample_rates"]),
                    bit_depths=tuple(raw["bit_depths"]),
                    max_channels=int(raw.get("max_channels", 2)),
                )
                _caps_cache[card] = caps
                return caps
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                pass

    caps: HwAudioCaps | None = None
    for codec_path in _read_codec_paths(card):
        parsed = _parse_codec_file(codec_path)
        if parsed is not None:
            caps = parsed
            break

    if caps is not None:
        _caps_cache[card] = caps
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "sample_rates": list(caps.sample_rates),
                        "bit_depths": list(caps.bit_depths),
                        "max_channels": caps.max_channels,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
    return caps

def caps_for_endpoint(
    endpoint_id: str | None,
    *,
    data_dir: Path | None = None,
) -> HwAudioCaps | None:
    from tunes_player.platform.linux.alsa_mixer import alsa_card_from_endpoint_id

    card = alsa_card_from_endpoint_id(endpoint_id or "")
    if card is None:
        return None
    return probe_card_caps(card, data_dir=data_dir)
