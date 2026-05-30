#!/usr/bin/env python3
"""Generate platform app icons from the SVG source at package build time."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

APP_ICON_ID = "tunes-player"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT / "data" / "icons" / f"{APP_ICON_ID}.svg"
DEFAULT_OUTPUT = REPO_ROOT / "build" / "icons"

LINUX_SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)


def _find_rsvg_convert() -> Path | None:
    path = shutil.which("rsvg-convert")
    return Path(path) if path else None


def _find_inkscape() -> Path | None:
    path = shutil.which("inkscape")
    return Path(path) if path else None


def _render_png(source: Path, size: int, destination: Path, *, tool: Path, kind: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if kind == "rsvg":
        subprocess.run(
            [str(tool), "-w", str(size), "-h", str(size), str(source), "-o", str(destination)],
            check=True,
        )
        return
    if kind == "inkscape":
        subprocess.run(
            [
                str(tool),
                str(source),
                "--export-type=png",
                f"--export-filename={destination}",
                f"--export-width={size}",
                f"--export-height={size}",
            ],
            check=True,
        )
        return
    raise ValueError(f"unknown renderer kind: {kind}")


def _pick_renderer() -> tuple[Path, str]:
    rsvg = _find_rsvg_convert()
    if rsvg is not None:
        return rsvg, "rsvg"
    inkscape = _find_inkscape()
    if inkscape is not None:
        return inkscape, "inkscape"
    print(
        "No SVG renderer found. Install one of:\n"
        "  Debian/Ubuntu: sudo apt install librsvg2-bin\n"
        "  Debian/Ubuntu: sudo apt install inkscape",
        file=sys.stderr,
    )
    raise SystemExit(1)


def generate_linux_hicolor(source: Path, output_dir: Path) -> None:
    tool, kind = _pick_renderer()
    hicolor = output_dir / "hicolor"
    scalable = hicolor / "scalable" / "apps" / f"{APP_ICON_ID}.svg"
    scalable.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, scalable)

    for size in LINUX_SIZES:
        destination = hicolor / f"{size}x{size}" / "apps" / f"{APP_ICON_ID}.png"
        _render_png(source, size, destination, tool=tool, kind=kind)
        print(f"  {destination.relative_to(output_dir)}")


def generate_macos_icns(source: Path, output_dir: Path) -> None:
    tool, kind = _pick_renderer()
    iconset = output_dir / f"{APP_ICON_ID}.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True)

    mac_sizes = (
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    )
    for size, name in mac_sizes:
        _render_png(source, size, iconset / name, tool=tool, kind=kind)

    icns_path = output_dir / f"{APP_ICON_ID}.icns"
    iconutil = shutil.which("iconutil")
    if iconutil is None:
        print(
            "iconutil not found (macOS only). Generated .iconset only:\n"
            f"  {iconset}",
            file=sys.stderr,
        )
        return
    subprocess.run([iconutil, "-c", "icns", str(iconset), "-o", str(icns_path)], check=True)
    print(f"  {icns_path.relative_to(output_dir)}")


def generate_windows_ico(source: Path, output_dir: Path) -> None:
    tool, kind = _pick_renderer()
    ico_sizes = (16, 24, 32, 48, 64, 128, 256)
    png_dir = output_dir / "windows-png"
    if png_dir.exists():
        shutil.rmtree(png_dir)
    png_dir.mkdir(parents=True)

    png_paths: list[Path] = []
    for size in ico_sizes:
        destination = png_dir / f"{size}.png"
        _render_png(source, size, destination, tool=tool, kind=kind)
        png_paths.append(destination)

    magick = shutil.which("magick") or shutil.which("convert")
    ico_path = output_dir / f"{APP_ICON_ID}.ico"
    if magick is None:
        print(
            "ImageMagick not found. Generated PNG layers only:\n"
            f"  {png_dir}",
            file=sys.stderr,
        )
        return

    command = [magick]
    command.extend(str(path) for path in png_paths)
    command.append(str(ico_path))
    subprocess.run(command, check=True)
    print(f"  {ico_path.relative_to(output_dir)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"SVG master icon (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Generated icon tree (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--platform",
        choices=("linux", "macos", "windows", "all"),
        default="linux",
        help="Target platform outputs (default: linux)",
    )
    args = parser.parse_args(argv)

    source = args.source.resolve()
    if not source.is_file():
        print(f"Icon source not found: {source}", file=sys.stderr)
        return 1

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    platforms = ("linux", "macos", "windows") if args.platform == "all" else (args.platform,)
    for platform in platforms:
        print(f"Generating {platform} icons into {output_dir}:")
        if platform == "linux":
            generate_linux_hicolor(source, output_dir)
        elif platform == "macos":
            generate_macos_icns(source, output_dir)
        elif platform == "windows":
            generate_windows_ico(source, output_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
