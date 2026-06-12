#!/usr/bin/env python3
"""Generate deterministic noise WAV fixtures for bit-perfect E2E tests."""

from __future__ import annotations

import argparse
import random
import struct
import wave
from array import array
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "tests" / "fixtures" / "bitperfect"

CHANNELS = 2
SEED = 0


def _load_matrix() -> tuple[tuple[int, int], ...]:
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from tests.bitperfect_matrix import FIXTURE_MATRIX

    return FIXTURE_MATRIX


def _load_fixture_layout(sample_rate: int) -> tuple[int, int, int]:
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from tests.bitperfect_matrix import (
        fixture_duration_sec,
        pattern_frame_count,
        pattern_start_frame,
    )

    total_frames = int(fixture_duration_sec(sample_rate) * sample_rate)
    return pattern_start_frame(sample_rate), pattern_frame_count(sample_rate), total_frames


def _noise_peak(bit_depth: int) -> int:
    if bit_depth <= 16:
        return (1 << 15) - 1
    if bit_depth == 32:
        return (1 << 31) - 1
    return (1 << 23) - 1


def _noise_frames(*, bit_depth: int, sample_rate: int, rng: random.Random) -> list[int]:
    pattern_start, pattern_frames, total_frames = _load_fixture_layout(sample_rate)
    peak = _noise_peak(bit_depth)
    samples: list[int] = []
    for frame in range(total_frames):
        for _channel in range(CHANNELS):
            if pattern_start <= frame < pattern_start + pattern_frames:
                samples.append(rng.randint(-peak, peak))
            else:
                samples.append(0)
    return samples


def _write_wav_16(path: Path, *, sample_rate: int, samples: list[int]) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        packed = struct.pack(f"<{len(samples)}h", *samples)
        handle.writeframes(packed)


def _write_wav_32(path: Path, *, sample_rate: int, samples: list[int]) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(4)
        handle.setframerate(sample_rate)
        handle.writeframes(array("i", samples).tobytes())


def _write_wav_24(path: Path, *, sample_rate: int, samples: list[int]) -> None:
    frame_count = len(samples) // CHANNELS
    data_size = frame_count * CHANNELS * 3
    byte_rate = sample_rate * CHANNELS * 3
    block_align = CHANNELS * 3
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        CHANNELS,
        sample_rate,
        byte_rate,
        block_align,
        24,
        b"data",
        data_size,
    )
    body = bytearray()
    for sample in samples:
        clamped = max(-(1 << 23), min((1 << 23) - 1, sample))
        if clamped < 0:
            clamped += 1 << 24
        body.extend(clamped.to_bytes(3, "little", signed=False))
    path.write_bytes(header + bytes(body))


def write_fixture(output_dir: Path, *, bit_depth: int, sample_rate: int) -> Path:
    rng = random.Random(SEED)
    samples = _noise_frames(bit_depth=bit_depth, sample_rate=sample_rate, rng=rng)
    path = output_dir / f"noise_{bit_depth}_{sample_rate}.wav"
    if bit_depth <= 16:
        _write_wav_16(path, sample_rate=sample_rate, samples=samples)
    elif bit_depth == 32:
        _write_wav_32(path, sample_rate=sample_rate, samples=samples)
    else:
        _write_wav_24(path, sample_rate=sample_rate, samples=samples)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Directory for generated WAV fixtures",
    )
    args = parser.parse_args()
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    for bit_depth, sample_rate in _load_matrix():
        path = write_fixture(output_dir, bit_depth=bit_depth, sample_rate=sample_rate)
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
