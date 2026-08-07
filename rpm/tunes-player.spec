Name:           tunes-player
Version:        1.0.0a1
Release:        1
Summary:        Tunes — music player for local files and streaming
License:        GPL-3.0-or-later
URL:            https://github.com/mbrennwa/tunes-player
Source0:        tunes-player-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3
BuildRequires:  python3-pip
BuildRequires:  python3-setuptools
BuildRequires:  librsvg2-tools

# Runtime Requires are rewritten by tools/build-rpm.sh from
# [tool.rpm].dnf_requires in pyproject.toml.
Requires:       python3 >= 3.11
Requires:       python3-pip
Requires:       python3-gobject
Requires:       python3-cairo
Requires:       gtk4
Requires:       libadwaita
Requires:       mpv-libs
Requires:       python3-mutagen
Requires:       python3-platformdirs
Recommends:     alsa-utils
Recommends:     xdg-utils
Recommends:     wireplumber
Recommends:     pipewire-pulseaudio
Recommends:     pulseaudio-utils

%description
Tunes plays local audio files and streaming catalogs from TIDAL and Qobuz
(Deezer planned) in a GNOME/GTK shell on Linux.

%prep
%setup -q

%build
# Wheelhouse is built in %%install so the packaged wheels match the install root.

%install
set -eu
rm -rf %{buildroot}

# Ship an offline wheelhouse; the venv is created on the target machine
# (%%post) so the Python minor version matches the target system.
# Wheel list comes from [tool.deb].pypi_wheelhouse (shared with DEB packaging).
WHEEL_DIR="%{buildroot}/usr/lib/tunes-player/wheels"
BUILD_VENV="%{_builddir}/tunes-player-%{version}-build-venv"
export PYTHONNOUSERSITE=1

rm -rf "$WHEEL_DIR" "$BUILD_VENV"
mkdir -p "$WHEEL_DIR"

python3 - <<'PY'
from pathlib import Path
import tomllib

cfg = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
pkgs = cfg.get("tool", {}).get("deb", {}).get("pypi_wheelhouse", [])
Path("pypi-requirements.txt").write_text(
    ("\n".join(pkgs) + "\n") if pkgs else "",
    encoding="utf-8",
)
print("PyPI wheelhouse:", ", ".join(pkgs) if pkgs else "(empty)")
PY

python3 -m venv "$BUILD_VENV"
"$BUILD_VENV/bin/python" -m pip install --upgrade pip wheel setuptools

if [ -s pypi-requirements.txt ]; then
	"$BUILD_VENV/bin/python" -m pip wheel --wheel-dir "$WHEEL_DIR" -r pypi-requirements.txt
fi

"$BUILD_VENV/bin/python" -m pip wheel --no-deps --wheel-dir "$WHEEL_DIR" .

install -Dm644 pypi-requirements.txt \
	%{buildroot}/usr/lib/tunes-player/pypi-requirements.txt

rm -rf "$BUILD_VENV"

if command -v rsvg-convert >/dev/null 2>&1 || command -v inkscape >/dev/null 2>&1; then
	python3 scripts/generate_icons.py --platform linux
elif [ ! -f build/icons/hicolor/48x48/apps/tunes-player.png ]; then
	echo "No SVG renderer and no pre-built icons. Install librsvg2-tools." >&2
	exit 1
fi

install -Dm755 debian/wrapper/tunes-player %{buildroot}/usr/bin/tunes-player
install -Dm644 data/tunes.player.desktop \
	%{buildroot}/usr/share/applications/tunes.player.desktop
mkdir -p %{buildroot}/usr/share/icons/hicolor
cp -a build/icons/hicolor/. %{buildroot}/usr/share/icons/hicolor/

%post
set -eu

VENV=/usr/lib/tunes-player/venv
WHEELS=/usr/lib/tunes-player/wheels

rm -rf "$VENV"
python3 -m venv --system-site-packages "$VENV"

"$VENV/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true
"$VENV/bin/python" -m pip install --upgrade pip >/dev/null 2>&1 || true

# Install PyPI stack from wheelhouse (tidalapi deps + python-mpv).
"$VENV/bin/python" -m pip install \
  --no-index \
  --find-links "$WHEELS" \
  -r /usr/lib/tunes-player/pypi-requirements.txt

"$VENV/bin/python" -m pip install \
  --no-index \
  --find-links "$WHEELS" \
  --no-deps \
  tunes-player

rm -f /usr/share/applications/tunes-player.desktop

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q -f -t /usr/share/icons/hicolor || true
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications || true
fi

%postun
set -eu
# $1 == 0 means the package is being removed (not upgraded).
if [ "$1" -eq 0 ]; then
  rm -rf /usr/lib/tunes-player/venv
fi

%files
%license LICENSE
%doc README.md
/usr/bin/tunes-player
/usr/lib/tunes-player/wheels/
/usr/lib/tunes-player/pypi-requirements.txt
/usr/share/applications/tunes.player.desktop
/usr/share/icons/hicolor/*/apps/tunes-player.png
/usr/share/icons/hicolor/scalable/apps/tunes-player.svg

%changelog
* Fri Aug 07 2026 Matthias Brennwald <mbrennwa@gmail.com> - 1.0.0a1-1
- Initial RPM package (#109).
