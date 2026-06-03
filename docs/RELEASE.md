# Branching and releases

Tunes Player uses two long-lived branches and short-lived fix branches. Version
numbers live in `pyproject.toml`; tags and packaging follow from there.

## Branches

| Branch | Purpose |
|--------|---------|
| **`main`** | Always matches the **last released** version. No work-in-progress. Every public release (including dev/pre-releases) is merged here, version-bumped, and tagged. |
| **`devel`** | Day-to-day development toward the **next** release. May be ahead of `main`. |
| **`bugfix/…`** | Short-lived branches from `main` for a single fix; delete after merge. |

In docs and PRs you may refer to `devel/1.0` when discussing a specific line —
that avoids confusion with version segments such as `1.0.dev0`.

## Version numbers

Format: `MAJOR.MINOR.PATCH` (e.g. `1.0.4`).

- **Major** — fundamental or breaking changes.
- **Minor** — new features or notable UI changes (non-breaking).
- **Patch** — bug-fix releases only (`1.0.1`, `1.0.2`, …).

**Pre-stable public testing** (released from `main`):

- `1.0.dev0`, `1.0.dev1`, … — increment `.devN` for each test release until
  ready for a stable `1.0.0`.

**Stable and maintenance:**

- First stable: `1.0.0`
- Bug fixes: `1.0.1`, `1.0.2`, …

Set the version only in **`pyproject.toml`**. Runtime and packaging should read it
from there (do not duplicate version strings elsewhere).

## Release workflow

### Dev / test release (`1.0.devN`)

1. Finish and test on **`devel`**.
2. Merge **`devel` → `main`**.
3. On **`main`**, set `version = "1.0.devN"` in `pyproject.toml` and commit.
4. Tag on **`main`**: `v1.0.devN`.
5. Create a **GitHub Release** from the tag; mark as **pre-release** for `.dev`
   versions.
6. Merge **`main` → `devel`** so the dev branch includes the released state.
7. Continue development on **`devel`**.

### Stable release (`1.0.0`, `1.1.0`, …)

Same as dev releases, but use a normal triplet (e.g. `1.0.0`) and do **not**
mark the GitHub Release as pre-release.

### Bug-fix release (`1.0.1`, …)

1. Branch from **`main`**: `bugfix/short-description`.
2. Fix, test, merge **`bugfix/…` → `main`**.
3. On **`main`**, bump the patch version in `pyproject.toml`, tag `v1.0.1`,
   create GitHub Release.
4. Merge **`main` → `devel`**.

## Git tags

- Format: `v` + version from `pyproject.toml` (e.g. `v1.0.dev0`, `v1.0.0`,
  `v1.0.1`).
- Tags are created on **`main`** at the release commit.

## Rules (summary)

- **`main` = last release** — dev, stable, or bugfix; not day-to-day feature work.
- **Feature work happens on `devel`** (or topic branches merged into `devel`).
- **Releases are cut only from `main`** after merging from `devel` or `bugfix/…`.
- After every release on `main`, **merge `main` into `devel`**.

## Packaging

DEB packages are built from **`main`** at a release tag.

- **Upstream version** comes from `pyproject.toml` (`[project].version`).
- **Debian revision** (`-1`, `-2`, …) is for packaging-only fixes without bumping
  the upstream version.
- **Build:** `./tools/build-deb.sh` (see [tools/howto-build-deb.txt](../tools/howto-build-deb.txt)).
  Artifacts land in `dist/`.
- **Target systems:** Debian 12+ or Ubuntu 24.04+ with Python 3.11+.
- **Dependencies:** `[tool.deb]` in `pyproject.toml` (`apt_depends`, `pypi_wheelhouse`).
- Attach the `.deb` from `dist/` to the GitHub Release for the matching tag.

## References

- Repository: [mbrennwa/tunes-player](https://github.com/mbrennwa/tunes-player)
- Planning: GitHub issue #10 (versioning / release cycle)
