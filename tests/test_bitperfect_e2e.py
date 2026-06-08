"""Bit-perfect end-to-end validation via ALSA loopback.

Requires:
  sudo modprobe snd-aloop pcm_substreams=1
  in-process libmpv (MpvEngine), arecord, aplay

Run (quit Tunes Player first — exclusive ALSA blocks loopback capture):
  source .venv/bin/activate
  python -m pytest -m integration tests/test_bitperfect_e2e.py -v

Each fixture is silence padding, then 0.2 s of deterministic noise, then more padding.
The test locates that noise burst in the loopback capture and compares it sample-for-sample.

See https://github.com/mbrennwa/tunes-player/issues/23
"""

from __future__ import annotations

import unittest

import pytest

from bitperfect_harness import (
    FIXTURES_DIR,
    clear_loopback_delivery_cache,
    find_loopback_devices,
    integration_skip_reason,
    release_loopback,
    run_bitperfect_case,
)
from bitperfect_matrix import all_fixture_filenames

_SKIP_REASON = integration_skip_reason()


def _make_fixture_test(fixture_name: str):
    def test(self) -> None:  # type: ignore[no-untyped-def]
        run_bitperfect_case(FIXTURES_DIR / fixture_name)

    return test


@pytest.mark.integration
@unittest.skipIf(_SKIP_REASON is not None, _SKIP_REASON or "")
class BitPerfectE2ETests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_loopback_delivery_cache()
        loopback = find_loopback_devices()
        if loopback is not None:
            release_loopback(loopback)

    def test_fixtures_dir_exists(self) -> None:
        self.assertTrue(FIXTURES_DIR.is_dir())
        for name in all_fixture_filenames():
            self.assertTrue((FIXTURES_DIR / name).is_file(), name)


for _fixture_name in all_fixture_filenames():
    _test_name = f"test_{_fixture_name.removesuffix('.wav')}"
    setattr(
        BitPerfectE2ETests,
        _test_name,
        _make_fixture_test(_fixture_name),
    )


if __name__ == "__main__":
    unittest.main()
