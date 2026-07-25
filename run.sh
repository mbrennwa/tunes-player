#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

if [[ ! -d .venv ]] || [[ ! -f .venv/bin/tunes-player ]]; then
  rm -rf .venv
  python3 -m venv .venv --system-site-packages
  .venv/bin/pip install -e .
else
  tunes_shebang="$(head -1 .venv/bin/tunes-player)"
  if [[ "$tunes_shebang" != "#!$ROOT/.venv/bin/python" \
    && "$tunes_shebang" != "#!$ROOT/.venv/bin/python3" ]] \
    || ! .venv/bin/python3 -c 'import tunes_player' 2>/dev/null; then
    rm -rf .venv
    python3 -m venv .venv --system-site-packages
    .venv/bin/pip install -e .
  fi
fi

install_desktop_integration() {
  local dest="$HOME/.local/share"
  local tunes_bin="$ROOT/.venv/bin/tunes-player"
  local desktop_file="$dest/applications/tunes.player.desktop"
  local icon_src="$ROOT/data/icons/tunes-player.svg"
  local icon_png="$dest/icons/hicolor/128x128/apps/tunes-player.png"
  local broken_hicolor_theme="$dest/icons/hicolor/index.theme"
  local stale_svg="$dest/icons/hicolor/scalable/apps/tunes-player.svg"

  mkdir -p "$dest/applications"

  # A previous version wrote a minimal hicolor index.theme here, which shadowed
  # the system hicolor fallback and broke icons for Firefox and other apps.
  if [[ -f "$broken_hicolor_theme" ]] \
    && grep -q '^Directories=scalable/apps,scalable/actions$' "$broken_hicolor_theme"; then
    rm -f "$broken_hicolor_theme" "$dest/icons/hicolor/icon-theme.cache"
    echo "tunes-player: removed broken local hicolor icon theme — restart GNOME Shell if other app icons still look wrong" >&2
  fi

  rm -f "$stale_svg"

  local icon_changed=0
  if [[ ! -f "$icon_src" ]]; then
    echo "tunes-player: missing icon source $icon_src" >&2
    exit 1
  fi
  if ! command -v rsvg-convert >/dev/null; then
    echo "tunes-player: rsvg-convert is required to install launcher icons" >&2
    exit 1
  fi

  # GNOME Shell's app switcher uses St, which often renders SVG app icons blank.
  # Install raster icons only and point the .desktop file at a PNG path.
  for size in 16 22 24 32 48 64 96 128 192 256; do
    local png_dir="$dest/icons/hicolor/${size}x${size}/apps"
    local png_installed="$png_dir/tunes-player.png"
    mkdir -p "$png_dir"
    if [[ ! -f "$png_installed" ]] || [[ "$icon_src" -nt "$png_installed" ]]; then
      icon_changed=1
      rsvg-convert -w "$size" -h "$size" "$icon_src" -o "$png_installed"
    fi
  done

  cat >"$desktop_file" <<EOF
[Desktop Entry]
Type=Application
Name=Tunes
Comment=Music player for local files and streaming (TIDAL, Qobuz)
Icon=$icon_png
StartupWMClass=tunes.player
TryExec=$tunes_bin
Exec=$tunes_bin
Terminal=false
Categories=AudioVideo;Audio;Player;
Keywords=music;audio;player;library;
StartupNotify=true
SingleMainWindow=true
EOF
  chmod 644 "$desktop_file"

  update-desktop-database "$dest/applications" 2>/dev/null || true
  if (( icon_changed )); then
    echo "tunes-player: icon updated — restart GNOME Shell if the launcher still shows the old icon" >&2
  fi
}

install_desktop_integration

exec .venv/bin/tunes-player "$@"
