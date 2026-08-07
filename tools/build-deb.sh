#!/usr/bin/env bash
set -euo pipefail

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "WARNING: git working tree has uncommitted changes."
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if [ -f debian/rules ]; then
  chmod +x debian/rules
fi

VERSION="$(python3 - <<'EOF'
from pathlib import Path
import tomllib

data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
print(data["project"]["version"])
EOF
)"

echo "Building tunes-player version: $VERSION"

CHANGELOG_UPSTREAM="$(head -1 debian/changelog | sed -n 's/^[^ (]* (\([^)-]*\).*/\1/p')"
if [ "$CHANGELOG_UPSTREAM" != "$VERSION" ]; then
  dch --newversion "$VERSION" "Automated build"
else
  echo "debian/changelog already at $VERSION (no change)."
fi

python3 - <<'PY'
from __future__ import annotations

from pathlib import Path

import tomllib

cfg = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
deb = cfg.get("tool", {}).get("deb", {})

apt_depends = deb.get("apt_depends", [])
pypi_wheelhouse = deb.get("pypi_wheelhouse", [])

if not apt_depends:
    raise SystemExit("ERROR: [tool.deb].apt_depends is missing or empty in pyproject.toml")

req = Path("debian/pypi-requirements.txt")
if pypi_wheelhouse:
    req.write_text("\n".join(pypi_wheelhouse) + "\n", encoding="utf-8")
    print("PyPI wheelhouse:", ", ".join(pypi_wheelhouse))
else:
    req.unlink(missing_ok=True)
    print("PyPI wheelhouse: (empty)")

control = Path("debian/control")
lines = control.read_text(encoding="utf-8").splitlines(True)

out: list[str] = []
in_pkg = False
depends_written = False
skip_depends_continuation = False
for line in lines:
    if line.startswith("Package:"):
        in_pkg = line.strip() == "Package: tunes-player"
        depends_written = False if in_pkg else depends_written

    if skip_depends_continuation:
        if line.startswith((" ", "\t")) and not line.strip().startswith("Description:"):
            continue
        skip_depends_continuation = False

    if in_pkg and line.startswith("Depends:"):
        joined = ", ".join(apt_depends)
        out.append("Depends: ${misc:Depends},\n")
        out.append("          " + joined.replace(", ", ",\n          ") + "\n")
        depends_written = True
        skip_depends_continuation = True
        continue

    out.append(line)

if not depends_written:
    raise SystemExit("ERROR: Could not find 'Depends:' line in debian/control for Package: tunes-player")

control.write_text("".join(out), encoding="utf-8")
print("Updated debian/control Depends for Package: tunes-player")
PY

DPKG_OPTS=(-us -uc)
# On non-Debian hosts (e.g. Fedora), Build-Depends names like librsvg2-bin /
# python3-venv are unmet even when the equivalent tools are installed.
if [ ! -f /etc/debian_version ]; then
  DPKG_OPTS+=(-d)
  echo "Non-Debian host: dpkg-buildpackage -d (ignore Debian Build-Depends names)."
fi
dpkg-buildpackage "${DPKG_OPTS[@]}"

mkdir -p dist
find dist -maxdepth 1 -type f -name 'tunes-player_*' -delete
shopt -s nullglob
artifacts=(../tunes-player_*)
if ((${#artifacts[@]} > 0)); then
  mv -t dist "${artifacts[@]}"
fi

echo "Artifacts in dist/:"
ls -1 dist
