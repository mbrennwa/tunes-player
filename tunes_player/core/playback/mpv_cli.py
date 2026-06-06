"""Build mpv command-line arguments from option dicts."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tunes_player.core.playback.output_profile import PlaybackOutputProfile


def base_audio_options(
    profile: PlaybackOutputProfile | None,
    use_device_output: bool,
) -> dict[str, object]:
    if profile is not None and profile.direct_alsa:
        opts: dict[str, object] = {"ao": "alsa", "replaygain": "no"}
        if profile.use_exclusive:
            opts["audio_exclusive"] = "yes"
        return opts
    if use_device_output:
        return {"ao": "pipewire,pulse,alsa,sndio"}
    return {"ao": "sndio,pulse,alsa,pipewire"}


def mpv_cli_args_from_options(options: dict[str, object]) -> list[str]:
    """Convert mpv property names/values to ``--key=value`` CLI flags."""
    args: list[str] = []
    for key, value in options.items():
        flag = key.replace("_", "-")
        if value is None:
            continue
        if isinstance(value, bool):
            args.append(f"--{flag}={'yes' if value else 'no'}")
        else:
            args.append(f"--{flag}={value}")
    return args
