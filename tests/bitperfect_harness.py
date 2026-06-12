"""Helpers for bit-perfect ALSA loopback integration tests."""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

import pytest

from bitperfect_matrix import (
    fixture_duration_sec,
    is_hires_sample_rate,
    pattern_frame_count,
    pattern_start_frame,
)
from tunes_player.core.config import ConfigManager
from tunes_player.core.library.db import connect
from tunes_player.core.library.scanner import LibraryScanner
from tunes_player.core.library.store import LibraryStore
from tunes_player.core.services import PlayerService
from tunes_player.engines.factory import probe_playback_engine
from tunes_player.platform.linux.audio import create_volume_controller

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "bitperfect"

_APLAY_CARD = re.compile(
    r"^card (\d+): ([^,]+?)(?:\s+\[([^\]]+)\])?, device (\d+): "
    r"(.+?)(?:\s+\[([^\]]+)\])?\s*$"
)
_HW_PARAMS_RATE = re.compile(r"^rate:\s*(\d+)")
_HW_PARAMS_FORMAT = re.compile(r"^format:\s*(\S+)")
_LOOPBACK_DELIVERY_CACHE: dict[tuple[int, int, int], bool] = {}


@dataclass(frozen=True, slots=True)
class WavPcm:
    samples: array
    sample_rate: int
    channels: int
    bit_depth: int


@dataclass(frozen=True, slots=True)
class CaptureStrategy:
    playback_first: bool
    recorder_after_hw_params: bool = False
    recorder_on_hw_match: bool = False
    use_plug_routing: bool = False


@dataclass(frozen=True, slots=True)
class LoopbackDevices:
    card: int
    playback_device: int
    endpoint_id: str
    mpv_device: str
    capture_device: str


@dataclass(frozen=True, slots=True)
class LoopbackAlsaRouting:
    """ALSA plug PCMs that pin rate/format on both loopback sides."""

    playback_pcm: str
    capture_pcm: str
    config_path: Path
    sample_format: str


class _RoutingVolumeController:
    """Route mpv through the pinned-rate plug playback PCM."""

    def __init__(self, inner: object, routing: LoopbackAlsaRouting) -> None:
        self._inner = inner
        self._routing = routing

    def mpv_audio_device(self) -> str | None:
        return f"alsa/{self._routing.playback_pcm}"

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def integration_skip_reason() -> str | None:
    if shutil.which("arecord") is None:
        return "arecord not found"
    if shutil.which("aplay") is None:
        return "aplay not found"
    if find_loopback_devices() is None:
        return "ALSA Loopback card not available (sudo modprobe snd-aloop)"
    mpv_error = probe_playback_engine()
    if mpv_error is not None:
        return mpv_error
    return None


def find_loopback_devices() -> LoopbackDevices | None:
    if shutil.which("aplay") is None:
        return None
    result = subprocess.run(
        ["aplay", "-l"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        match = _APLAY_CARD.match(line.strip())
        if match is None:
            continue
        card = int(match.group(1))
        card_name = match.group(2).strip()
        card_long = match.group(3) or ""
        device = int(match.group(4))
        haystack = f"{card_name} {card_long}".casefold()
        if "loopback" not in haystack:
            continue
        return LoopbackDevices(
            card=card,
            playback_device=device,
            endpoint_id=f"alsa:hw:{card}:{device}",
            mpv_device=f"hw:{card},{device}",
            capture_device=f"hw:{card},1,0",
        )
    return None


def loopback_available() -> bool:
    return find_loopback_devices() is not None


def release_loopback(loopback: LoopbackDevices, *, timeout_sec: float = 2.0) -> None:
    """Wait for loopback playback PCM to close between tests."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not read_hw_params(loopback.card, device=loopback.playback_device).get("rate"):
            time.sleep(0.1)
            return
        time.sleep(0.05)
    time.sleep(0.2)


def _invalidate_delivery_cache(
    loopback: LoopbackDevices,
    *,
    sample_rate: int,
    bit_depth: int,
) -> None:
    _LOOPBACK_DELIVERY_CACHE.pop((loopback.card, sample_rate, bit_depth), None)


def clear_loopback_delivery_cache() -> None:
    _LOOPBACK_DELIVERY_CACHE.clear()


def alsa_capture_format(bit_depth: int) -> str:
    """ALSA format for arecord during mpv playback."""
    if bit_depth <= 16:
        return "S16_LE"
    return "S32_LE"


def alsa_smoke_capture_format(bit_depth: int) -> str:
    """ALSA format for arecord during aplay smoke tests."""
    if bit_depth <= 16:
        return "S16_LE"
    if bit_depth == 32:
        return "S32_LE"
    return "S24_3LE"


def create_loopback_alsa_routing(
    workspace: Path,
    loopback: LoopbackDevices,
    *,
    sample_rate: int,
    sample_format: str,
) -> LoopbackAlsaRouting:
    config_path = workspace / ".asoundrc"
    config_path.write_text(
        (
            "pcm.tunes_bp_play {\n"
            "  type plug\n"
            "  slave {\n"
            f'    pcm "{loopback.mpv_device}"\n'
            f"    rate {sample_rate}\n"
            f"    format {sample_format}\n"
            "    channels 2\n"
            "  }\n"
            "}\n"
            "pcm.tunes_bp_cap {\n"
            "  type plug\n"
            "  slave {\n"
            f'    pcm "{loopback.capture_device}"\n'
            f"    rate {sample_rate}\n"
            f"    format {sample_format}\n"
            "    channels 2\n"
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    return LoopbackAlsaRouting(
        playback_pcm="tunes_bp_play",
        capture_pcm="tunes_bp_cap",
        config_path=config_path,
        sample_format=sample_format,
    )


def _apply_capture_routing(context: BitPerfectTestContext, *, use_plug: bool) -> None:
    if use_plug:
        context._saved_alsa_config = os.environ.get("ALSA_CONFIG_PATH")
        os.environ["ALSA_CONFIG_PATH"] = str(context.plug_routing.config_path)
        if context._base_volume_controller is not None:
            context.service._volume_controller = _RoutingVolumeController(
                context._base_volume_controller,
                context.plug_routing,
            )
    else:
        _restore_capture_routing(context)


def _restore_capture_routing(context: BitPerfectTestContext) -> None:
    saved = context._saved_alsa_config
    if saved is None:
        os.environ.pop("ALSA_CONFIG_PATH", None)
    else:
        os.environ["ALSA_CONFIG_PATH"] = saved
    if context._base_volume_controller is not None:
        context.service._volume_controller = context._base_volume_controller


def _reset_playback_engine(service: PlayerService) -> None:
    if service._engine is not None:
        service._engine.quit()
        service._engine = None


def _sign_extend_24(value: int) -> int:
    if value & 0x800000:
        value -= 1 << 24
    return value


def _read_wav_32(handle: wave.Wave_read) -> WavPcm:
    channels = handle.getnchannels()
    sample_rate = handle.getframerate()
    frame_count = handle.getnframes()
    raw = handle.readframes(frame_count)
    samples = array("i")
    samples.frombytes(raw)
    if handle.getsampwidth() != 4:
        raise ValueError(f"unsupported 32-bit width: {handle.getsampwidth()}")
    return WavPcm(
        samples=samples,
        sample_rate=sample_rate,
        channels=channels,
        bit_depth=32,
    )


def _read_wav_16(handle: wave.Wave_read) -> WavPcm:
    channels = handle.getnchannels()
    sample_rate = handle.getframerate()
    frame_count = handle.getnframes()
    raw = handle.readframes(frame_count)
    samples = array("h")
    samples.frombytes(raw)
    if handle.getsampwidth() != 2:
        raise ValueError(f"unsupported 16-bit width: {handle.getsampwidth()}")
    return WavPcm(
        samples=samples,
        sample_rate=sample_rate,
        channels=channels,
        bit_depth=16,
    )


def _read_wav_24(path: Path) -> WavPcm:
    data = path.read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError(f"not a WAV file: {path}")
    channels = struct.unpack_from("<H", data, 22)[0]
    sample_rate = struct.unpack_from("<I", data, 24)[0]
    bits = struct.unpack_from("<H", data, 34)[0]
    if bits != 24:
        raise ValueError(f"expected 24-bit WAV, got {bits}")
    offset = 12
    pcm_bytes = b""
    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", data, offset + 4)[0]
        chunk_data = data[offset + 8 : offset + 8 + chunk_size]
        if chunk_id == b"data":
            pcm_bytes = chunk_data
            break
        offset += 8 + chunk_size
    if not pcm_bytes:
        raise ValueError(f"no data chunk in {path}")
    samples = array("i")
    for index in range(0, len(pcm_bytes), 3):
        raw = int.from_bytes(pcm_bytes[index : index + 3], "little")
        samples.append(_sign_extend_24(raw))
    return WavPcm(
        samples=samples,
        sample_rate=sample_rate,
        channels=channels,
        bit_depth=24,
    )


def read_wav_pcm(path: Path) -> WavPcm:
    with wave.open(str(path), "rb") as handle:
        sample_width = handle.getsampwidth()
        if sample_width == 2:
            return _read_wav_16(handle)
        if sample_width == 4:
            return _read_wav_32(handle)
    return _read_wav_24(path)


def _normalize_capture_to_reference(
    reference: WavPcm, captured: WavPcm
) -> WavPcm:
    """Map S32_LE loopback capture to the reference PCM representation."""
    if reference.bit_depth <= 16 or reference.bit_depth == 32:
        return captured
    if captured.bit_depth == 32:
        normalized = array("i", (sample >> 8 for sample in captured.samples))
        return WavPcm(
            samples=normalized,
            sample_rate=captured.sample_rate,
            channels=captured.channels,
            bit_depth=24,
        )
    return captured


def _pcm_peak(mono: list[int]) -> int:
    if not mono:
        return 0
    return max(abs(sample) for sample in mono)


def _assert_capture_not_silent(captured: WavPcm) -> None:
    mono = _mono_channel(captured.samples, captured.channels)
    if not mono:
        raise AssertionError("Captured PCM is empty")
    if _pcm_peak(mono) == 0:
        raise AssertionError(
            "Captured PCM is silent. Stop Tunes Player (and other audio apps) "
            f"before running integration tests (rate={captured.sample_rate} Hz)."
        )


def _to_compare_array(pcm: WavPcm) -> array:
    if pcm.bit_depth <= 16:
        return pcm.samples
    return pcm.samples


def _mono_channel(samples: array, channels: int) -> list[int]:
    if channels <= 1:
        return list(samples)
    return [samples[index] for index in range(0, len(samples), channels)]


def align_pcm(
    reference: WavPcm,
    captured: WavPcm,
) -> tuple[array, array]:
    """Find the 0.2 s noise pattern in capture and return that slice for compare."""
    if reference.channels != captured.channels:
        raise AssertionError(
            f"channel mismatch: ref={reference.channels} cap={captured.channels}"
        )
    ref = _to_compare_array(reference)
    cap = _to_compare_array(captured)
    ref_mono = _mono_channel(ref, reference.channels)
    cap_mono = _mono_channel(cap, captured.channels)

    pattern_start = pattern_start_frame(reference.sample_rate)
    pattern_frames = pattern_frame_count(reference.sample_rate)
    if pattern_start + pattern_frames > len(ref_mono):
        raise AssertionError("reference fixture is shorter than the noise pattern window")
    pattern_mono = ref_mono[pattern_start : pattern_start + pattern_frames]

    window_frames = min(256, pattern_frames)
    # Search the whole capture: the pattern may appear after latency padding.
    search_lag = max(0, len(cap_mono) - window_frames)
    best_lag = 0
    best_score = float("inf")
    for lag in range(search_lag + 1):
        score = 0
        for index in range(window_frames):
            delta = pattern_mono[index] - cap_mono[index + lag]
            score += abs(delta)
        if score < best_score:
            best_score = score
            best_lag = lag

    zero_capture_score = sum(abs(pattern_mono[index]) for index in range(window_frames))
    if best_score >= zero_capture_score * 0.5:
        raise AssertionError(
            "Capture does not correlate with the reference — loopback output "
            "may be silent or the sample rate may not be supported."
        )

    if best_lag + pattern_frames > len(cap_mono):
        raise AssertionError(
            "Capture ended before the full 0.2 s noise pattern could be compared"
        )
    if _pcm_peak(cap_mono[best_lag : best_lag + pattern_frames]) == 0:
        raise AssertionError(
            "Capture does not correlate with the reference — loopback output "
            "may be silent or the sample rate may not be supported."
        )

    pattern_ref = ref[
        pattern_start * reference.channels : (pattern_start + pattern_frames)
        * reference.channels
    ]
    pattern_cap = cap[
        best_lag * captured.channels : (best_lag + pattern_frames) * captured.channels
    ]
    return pattern_ref, pattern_cap


def _assert_aligned_equal(ref_aligned: array, cap_aligned: array) -> None:
    if len(ref_aligned) != len(cap_aligned):
        raise AssertionError(
            f"aligned sample length mismatch: {len(ref_aligned)} vs {len(cap_aligned)}"
        )
    if _pcm_peak(list(cap_aligned)) == 0 and _pcm_peak(list(ref_aligned)) > 0:
        raise AssertionError(
            "Capture aligned PCM is silent — loopback did not deliver audio."
        )
    max_diff = 0
    mismatch_index = -1
    for index, (left, right) in enumerate(zip(ref_aligned, cap_aligned, strict=False)):
        diff = abs(left - right)
        if diff > max_diff:
            max_diff = diff
            mismatch_index = index
    if max_diff != 0:
        raise AssertionError(
            "PCM mismatch after alignment: "
            f"max_abs_diff={max_diff} at sample {mismatch_index}"
        )


def assert_pcm_equal(reference: WavPcm, captured: WavPcm) -> None:
    captured = _normalize_capture_to_reference(reference, captured)
    _assert_capture_not_silent(captured)
    ref_aligned, cap_aligned = align_pcm(reference, captured)
    _assert_aligned_equal(ref_aligned, cap_aligned)


def read_hw_params(card: int, *, device: int = 0) -> dict[str, str]:
    path = Path(f"/proc/asound/card{card}/pcm{device}p/sub0/hw_params")
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        rate_match = _HW_PARAMS_RATE.match(line.strip())
        if rate_match is not None:
            values["rate"] = rate_match.group(1)
            continue
        format_match = _HW_PARAMS_FORMAT.match(line.strip())
        if format_match is not None:
            values["format"] = format_match.group(1)
    return values


def _wait_for_hw_params(
    loopback: LoopbackDevices,
    *,
    sample_rate: int,
    timeout_sec: float = 3.0,
) -> dict[str, str]:
    deadline = time.monotonic() + timeout_sec
    latest: dict[str, str] = {}
    while time.monotonic() < deadline:
        latest = read_hw_params(loopback.card, device=loopback.playback_device)
        if latest.get("rate") == str(sample_rate):
            return latest
        time.sleep(0.05)
    return latest


def assert_hw_params_match(
    hw_params: dict[str, str],
    *,
    sample_rate: int,
    bit_depth: int,
    card: int,
) -> None:
    if not hw_params.get("rate"):
        raise AssertionError(
            f"No ALSA hw_params on card {card} during playback. "
            "Stop Tunes Player and other apps using the loopback device, then retry."
        )
    expected_format = alsa_capture_format(bit_depth)
    if hw_params.get("rate") != str(sample_rate):
        raise AssertionError(
            f"hw_params rate mismatch: {hw_params.get('rate')} != {sample_rate}"
        )
    if hw_params.get("format") != expected_format:
        raise AssertionError(
            f"hw_params format mismatch: {hw_params.get('format')} != {expected_format}"
        )


class AudioRecorder:
    def __init__(
        self,
        *,
        device: str,
        output_path: Path,
        sample_rate: int,
        sample_format: str,
        channels: int,
        alsa_config_path: Path | None = None,
    ) -> None:
        env = os.environ.copy()
        if alsa_config_path is not None:
            env["ALSA_CONFIG_PATH"] = str(alsa_config_path)
        self._process = subprocess.Popen(
            [
                "arecord",
                "-D",
                device,
                "-f",
                sample_format,
                "-r",
                str(sample_rate),
                "-c",
                str(channels),
                "-t",
                "wav",
                str(output_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.output_path = output_path

    def stop(self) -> None:
        if self._process.poll() is not None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=2)

    @property
    def returncode(self) -> int | None:
        return self._process.poll()


def _playback_timeout_sec(reference: WavPcm) -> float:
    frames = len(reference.samples) // max(reference.channels, 1)
    duration = frames / reference.sample_rate
    # Hi-res mpv/ALSA open can take several seconds on loopback.
    return duration + (12.0 if is_hires_sample_rate(reference.sample_rate) else 6.0)


def wait_for_playback(service: PlayerService, *, timeout_sec: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        service.poll_playback()
        state = service.get_playback_state()
        if state.current_track is None:
            break
        if state.duration_sec is not None and state.position_sec >= state.duration_sec - 0.05:
            service.poll_playback()
            time.sleep(0.1)
            service.poll_playback()
            break
        if not state.is_playing and state.position_sec > 0:
            break
        time.sleep(0.05)
    else:
        raise TimeoutError("playback did not finish before timeout")


@dataclass
class BitPerfectTestContext:
    workspace: Path
    config: ConfigManager
    service: PlayerService
    loopback: LoopbackDevices
    db_path: Path
    plug_routing: LoopbackAlsaRouting
    _base_volume_controller: object | None = None
    _saved_alsa_config: str | None = None


def track_id_for_path(db_path: Path, file_path: Path) -> str:
    connection = connect(db_path)
    try:
        row = connection.execute(
            """
            SELECT t.id
            FROM tracks t
            JOIN files f ON f.id = t.file_id
            WHERE f.path = ?
            """,
            (str(file_path.resolve()),),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError(f"no indexed track for {file_path}")
    return str(row["id"])


def _recorder_kwargs(
    context: BitPerfectTestContext,
    reference: WavPcm,
    *,
    use_plug: bool,
) -> dict[str, object]:
    if use_plug:
        return {
            "device": context.plug_routing.capture_pcm,
            "sample_rate": reference.sample_rate,
            "sample_format": alsa_capture_format(reference.bit_depth),
            "channels": reference.channels,
            "alsa_config_path": context.plug_routing.config_path,
        }
    return {
        "device": context.loopback.capture_device,
        "sample_rate": reference.sample_rate,
        "sample_format": alsa_capture_format(reference.bit_depth),
        "channels": reference.channels,
        "alsa_config_path": None,
    }


def _smoke_loopback_capture(
    loopback: LoopbackDevices,
    *,
    fixture_path: Path,
    capture_path: Path,
    sample_rate: int,
    bit_depth: int,
    playback_first: bool,
) -> bool:
    """Route fixture audio through loopback and check capture is non-silent."""
    aplay = subprocess.Popen(
        [
            "aplay",
            "-D",
            loopback.mpv_device,
            "-d",
            "1",
            "-q",
            str(fixture_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    recorder: AudioRecorder | None = None
    smoke_format = alsa_smoke_capture_format(bit_depth)
    try:
        if not playback_first:
            recorder = AudioRecorder(
                device=loopback.capture_device,
                output_path=capture_path,
                sample_rate=sample_rate,
                sample_format=smoke_format,
                channels=2,
            )
            time.sleep(0.1)
        else:
            time.sleep(0.1)
            recorder = AudioRecorder(
                device=loopback.capture_device,
                output_path=capture_path,
                sample_rate=sample_rate,
                sample_format=smoke_format,
                channels=2,
            )
        aplay.wait(timeout=fixture_duration_sec(sample_rate) + 8.0)
        time.sleep(0.1)
    finally:
        if aplay.poll() is None:
            aplay.terminate()
            aplay.wait(timeout=2)
        if recorder is not None:
            recorder.stop()
    if not capture_path.is_file():
        return False
    pcm = read_wav_pcm(capture_path)
    mono = _mono_channel(pcm.samples, pcm.channels)
    return _pcm_peak(mono) > 0


def loopback_delivers_audio(
    loopback: LoopbackDevices,
    *,
    sample_rate: int,
    bit_depth: int,
    fixture_path: Path,
) -> bool:
    """Smoke-test whether loopback routes audio at this rate (cached per card/rate)."""
    cache_key = (loopback.card, sample_rate, bit_depth)
    cached = _LOOPBACK_DELIVERY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    with tempfile.TemporaryDirectory(prefix="tunes-bitperfect-smoke-") as tmp:
        capture_path = Path(tmp) / "smoke.wav"
        ok = False
        for playback_first in (True, False):
            if capture_path.is_file():
                capture_path.unlink()
            release_loopback(loopback)
            ok = _smoke_loopback_capture(
                loopback,
                fixture_path=fixture_path,
                capture_path=capture_path,
                sample_rate=sample_rate,
                bit_depth=bit_depth,
                playback_first=playback_first,
            )
            if ok:
                break
        _LOOPBACK_DELIVERY_CACHE[cache_key] = ok
        return ok


def build_test_service(
    fixture_path: Path,
    loopback: LoopbackDevices,
) -> BitPerfectTestContext:
    workspace = Path(tempfile.mkdtemp(prefix="tunes-bitperfect-"))
    reference = read_wav_pcm(fixture_path)
    routing = create_loopback_alsa_routing(
        workspace,
        loopback,
        sample_rate=reference.sample_rate,
        sample_format=alsa_capture_format(reference.bit_depth),
    )
    music_dir = workspace / "music"
    music_dir.mkdir()
    target = music_dir / fixture_path.name
    target.write_bytes(fixture_path.read_bytes())

    config = ConfigManager(workspace / "config.json")
    config.load()
    config.config.music_folders = [str(music_dir.resolve())]
    config.config.output_sink_id = loopback.endpoint_id
    config.config.allow_software_volume_fallback = False
    config.config.exclusive_device_access = False
    config.save()

    db_path = workspace / "library.db"
    scanner = LibraryScanner(db_path=db_path, config=config.config)
    result = scanner.scan(scan_folders=[str(music_dir.resolve())])
    if result.indexed != 1:
        raise RuntimeError(f"expected one indexed track, got {result}")

    volume_controller = create_volume_controller(config.config)
    volume_controller.set_active_endpoint(loopback.endpoint_id)
    volume_controller.set_level(1.0)

    service = PlayerService(config=config, volume_controller=volume_controller)
    service._store = LibraryStore(db_path)
    service.set_exclusive_device_access(False)

    return BitPerfectTestContext(
        workspace=workspace,
        config=config,
        service=service,
        loopback=loopback,
        db_path=db_path,
        plug_routing=routing,
        _base_volume_controller=volume_controller,
    )


def _stop_playback(service: PlayerService) -> None:
    engine = service._engine
    if engine is not None:
        engine.stop()


def _is_loopback_capture_failure(exc: AssertionError) -> bool:
    message = str(exc).casefold()
    return "correlate" in message or "silent" in message


def _capture_strategies(sample_rate: int) -> tuple[CaptureStrategy, ...]:
    direct = (
        CaptureStrategy(playback_first=True),
        CaptureStrategy(playback_first=False),
    )
    if not is_hires_sample_rate(sample_rate):
        return direct
    direct_hires = (
        CaptureStrategy(playback_first=False),
        CaptureStrategy(playback_first=True),
        CaptureStrategy(playback_first=True, recorder_on_hw_match=True),
    )
    plug_hires = tuple(
        CaptureStrategy(
            playback_first=strategy.playback_first,
            recorder_on_hw_match=strategy.recorder_on_hw_match,
            use_plug_routing=True,
        )
        for strategy in direct_hires
    )
    return direct_hires + plug_hires


def _capture_during_mpv_playback(
    context: BitPerfectTestContext,
    *,
    track_id: str,
    reference: WavPcm,
    loopback: LoopbackDevices,
    capture_path: Path,
    strategy: CaptureStrategy,
) -> tuple[WavPcm, dict[str, str]]:
    """Record loopback capture while PlayerService plays the fixture."""
    recorder: AudioRecorder | None = None
    if capture_path.is_file():
        capture_path.unlink()
    playback_timeout = _playback_timeout_sec(reference)
    hw_timeout = 8.0 if is_hires_sample_rate(reference.sample_rate) else 3.0
    recorder_kwargs = _recorder_kwargs(
        context,
        reference,
        use_plug=strategy.use_plug_routing,
    )
    try:
        _apply_capture_routing(context, use_plug=strategy.use_plug_routing)
        _reset_playback_engine(context.service)
        if strategy.recorder_on_hw_match:
            context.service.play_track(track_id)
            hw_params: dict[str, str] = {}
            deadline = time.monotonic() + hw_timeout
            while recorder is None and time.monotonic() < deadline:
                hw_params = read_hw_params(
                    loopback.card,
                    device=loopback.playback_device,
                )
                if hw_params.get("rate") == str(reference.sample_rate):
                    recorder = AudioRecorder(
                        output_path=capture_path,
                        **recorder_kwargs,
                    )
                    break
                time.sleep(0.05)
            if recorder is None:
                hw_params = _wait_for_hw_params(
                    loopback,
                    sample_rate=reference.sample_rate,
                    timeout_sec=hw_timeout,
                )
                recorder = AudioRecorder(
                    output_path=capture_path,
                    **recorder_kwargs,
                )
        elif strategy.recorder_after_hw_params:
            context.service.play_track(track_id)
            hw_params = _wait_for_hw_params(
                loopback,
                sample_rate=reference.sample_rate,
                timeout_sec=hw_timeout,
            )
            recorder = AudioRecorder(
                output_path=capture_path,
                **recorder_kwargs,
            )
        elif not strategy.playback_first:
            recorder = AudioRecorder(
                output_path=capture_path,
                **recorder_kwargs,
            )
            time.sleep(0.1)
            context.service.play_track(track_id)
            hw_params = _wait_for_hw_params(
                loopback,
                sample_rate=reference.sample_rate,
                timeout_sec=hw_timeout,
            )
        else:
            context.service.play_track(track_id)
            time.sleep(0.1)
            recorder = AudioRecorder(
                output_path=capture_path,
                **recorder_kwargs,
            )
            hw_params = _wait_for_hw_params(
                loopback,
                sample_rate=reference.sample_rate,
                timeout_sec=hw_timeout,
            )
        wait_for_playback(context.service, timeout_sec=playback_timeout)
        recorder.stop()
        recorder = None
        if not capture_path.is_file():
            raise RuntimeError("capture file was not created")
        captured = read_wav_pcm(capture_path)
        assert_hw_params_match(
            hw_params,
            sample_rate=reference.sample_rate,
            bit_depth=reference.bit_depth,
            card=loopback.card,
        )
        return captured, hw_params
    finally:
        if recorder is not None:
            recorder.stop()
        _restore_capture_routing(context)


def run_bitperfect_case(fixture_path: Path) -> None:
    loopback = find_loopback_devices()
    if loopback is None:
        raise RuntimeError("loopback device not found")
    reference = read_wav_pcm(fixture_path)
    if not loopback_delivers_audio(
        loopback,
        sample_rate=reference.sample_rate,
        bit_depth=reference.bit_depth,
        fixture_path=fixture_path,
    ):
        pytest.skip(
            f"ALSA loopback does not deliver audio at {reference.sample_rate} Hz "
            "on this system (snd-aloop limitation)."
        )
    context = build_test_service(fixture_path, loopback)
    capture_path = context.workspace / "captured.wav"
    track_id = track_id_for_path(
        context.db_path,
        context.workspace / "music" / fixture_path.name,
    )
    try:
        for strategy in _capture_strategies(reference.sample_rate):
            release_loopback(loopback)
            try:
                captured, _hw_params = _capture_during_mpv_playback(
                    context,
                    track_id=track_id,
                    reference=reference,
                    loopback=loopback,
                    capture_path=capture_path,
                    strategy=strategy,
                )
                assert_pcm_equal(reference, captured)
                return
            except AssertionError as exc:
                if not _is_loopback_capture_failure(exc):
                    raise
                _stop_playback(context.service)
                time.sleep(0.1)
        _invalidate_delivery_cache(
            loopback,
            sample_rate=reference.sample_rate,
            bit_depth=reference.bit_depth,
        )
        pytest.fail(
            f"Loopback PCM capture did not match the source at "
            f"{reference.sample_rate} Hz / {reference.bit_depth}-bit. "
            "Quit Tunes Player, reload snd-aloop "
            "(sudo modprobe -r snd-aloop && sudo modprobe snd-aloop pcm_substreams=1), "
            "then retry."
        )
    finally:
        _restore_capture_routing(context)
        context.service.shutdown()
        release_loopback(loopback)


def cleanup_context(context: BitPerfectTestContext) -> None:
    context.service.shutdown()
    shutil.rmtree(context.workspace, ignore_errors=True)
