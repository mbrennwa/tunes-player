"""Build mpv command-line arguments from option dicts."""

from __future__ import annotations


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
