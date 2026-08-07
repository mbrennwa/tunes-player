#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

./tools/build-deb.sh
./tools/build-rpm.sh

echo "Packages in dist/:"
ls -1 dist/tunes-player_*.deb dist/tunes-player-*.rpm
