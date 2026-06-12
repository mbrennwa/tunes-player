"""Shared bit-perfect E2E fixture matrix and timing helpers."""

from __future__ import annotations

# Distinct signal under test: 0.2 s of deterministic noise per channel.
PATTERN_DURATION_SEC = 0.2

# Silence before/after the 0.2 s pattern (capture-first needs less than hw_params-wait paths).
STANDARD_PRE_ROLL_SEC = 0.15
STANDARD_POST_ROLL_SEC = 0.1
HIRES_PRE_ROLL_SEC = 0.25
HIRES_POST_ROLL_SEC = 0.15
HIRES_RATE_THRESHOLD_HZ = 88_200

_SAMPLE_RATES: tuple[int, ...] = (
    44100,
    48000,
    88200,
    96000,
    176400,
    192000,
    352800,
)
_BIT_DEPTHS: tuple[int, ...] = (16, 24, 32)

# CD / standard desktop rates plus audiophile hi-res (88.2 kHz and above).
FIXTURE_MATRIX: tuple[tuple[int, int], ...] = tuple(
    (bit_depth, sample_rate)
    for sample_rate in _SAMPLE_RATES
    for bit_depth in _BIT_DEPTHS
)


def fixture_filename(bit_depth: int, sample_rate: int) -> str:
    return f"noise_{bit_depth}_{sample_rate}.wav"


def is_hires_sample_rate(sample_rate: int) -> bool:
    return sample_rate >= HIRES_RATE_THRESHOLD_HZ


def pre_roll_sec(sample_rate: int) -> float:
    if is_hires_sample_rate(sample_rate):
        return HIRES_PRE_ROLL_SEC
    return STANDARD_PRE_ROLL_SEC


def post_roll_sec(sample_rate: int) -> float:
    if is_hires_sample_rate(sample_rate):
        return HIRES_POST_ROLL_SEC
    return STANDARD_POST_ROLL_SEC


def fixture_duration_sec(sample_rate: int) -> float:
    return pre_roll_sec(sample_rate) + PATTERN_DURATION_SEC + post_roll_sec(sample_rate)


def pattern_frame_count(sample_rate: int) -> int:
    return int(PATTERN_DURATION_SEC * sample_rate)


def pattern_start_frame(sample_rate: int) -> int:
    return int(pre_roll_sec(sample_rate) * sample_rate)


def all_fixture_filenames() -> tuple[str, ...]:
    return tuple(
        fixture_filename(bit_depth, sample_rate)
        for bit_depth, sample_rate in FIXTURE_MATRIX
    )
