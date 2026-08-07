# Branching and releases

Tunes Player uses two long-lived branches and short-lived fix branches. Version
numbers live in `pyproject.toml`; tags and packaging follow from there.

## Branches

| Branch | Purpose |
|--------|---------|
| **`main`** | Always matches the **last released** version. No work-in-progress. Every public release (including alpha/pre-releases) is merged here, version-bumped, and tagged. |
| **`devel`** | Day-to-day development toward the **next** release. May be ahead of `main`. |
| **`bugfix/…`** | Short-lived branches from `main` for a single fix; delete after merge. |

In docs and PRs you may refer to `devel/1.0` when discussing a specific line —
that avoids confusion with version segments such as `1.0.0a1`.

## Version numbers

Format: `MAJOR.MINOR.PATCH` (e.g. `1.0.4`).

- **Major** — fundamental or breaking changes.
- **Minor** — new features or notable UI changes (non-breaking).
- **Patch** — bug-fix releases only (`1.0.1`, `1.0.2`, …).

**Pre-stable public testing** (released from `main`):

Use [PEP 440](https://peps.python.org/pep-0440/) pre-release segments on the
full triplet (same scheme as [post](https://github.com/mbrennwa/post)):

- `1.0.0a1`, `1.0.0a2`, … — public alpha (`aN` starts at **1**)
- `1.0.0bN` — public beta (if needed)
- `1.0.0rcN` — release candidate (if needed)
- Earlier public tags used `.devN` (`v1.0.dev0`, `v1.0.dev1`); new cuts use
  `aN` / `bN` / `rcN`.

**Stable and maintenance:**

- First stable: `1.0.0`
- Bug fixes: `1.0.1`, `1.0.2`, …

Set the version only in **`pyproject.toml`**. Runtime and packaging should read it
from there (do not duplicate version strings elsewhere). Match
`debian/changelog` upstream version when cutting a release.

GitHub milestones for a public alpha cut use `Release_alpha-N` (e.g.
`Release_alpha-1` for `1.0.0a1`).

## Release workflow

### Alpha / test release (`1.0.0aN`)

1. Finish and test on **`devel`**.
2. Merge **`devel` → `main`**.
3. On **`main`**, set `version = "1.0.0aN"` in `pyproject.toml` and commit
   (update `debian/changelog` to match).
4. Tag on **`main`**: `v1.0.0aN`.
5. Create a **GitHub Release** from the tag; mark as **pre-release** for alpha
   versions (CI does this for `aN` / `bN` / `rcN` / legacy `.devN`).
6. Merge **`main` → `devel`** so the devel branch includes the released state.
7. Continue development on **`devel`**.

### Stable release (`1.0.0`, `1.1.0`, …)

Same as alpha releases, but use a normal triplet (e.g. `1.0.0`) and do **not**
mark the GitHub Release as pre-release.

### Bug-fix release (`1.0.1`, …)

1. Branch from **`main`**: `bugfix/short-description`.
2. Fix, test, merge **`bugfix/…` → `main`**.
3. On **`main`**, bump the patch version in `pyproject.toml`, tag `v1.0.1`,
   create GitHub Release.
4. Merge **`main` → `devel`**.

## Git tags

- Format: `v` + version from `pyproject.toml` (e.g. `v1.0.0a1`, `v1.0.0`,
  `v1.0.1`).
- Tags are created on **`main`** at the release commit.

## Rules (summary)

- **`main` = last release** — alpha, stable, or bugfix; not day-to-day feature work.
- **Feature work happens on `devel`** (or topic branches merged into `devel`).
- **Releases are cut only from `main`** after merging from `devel` or `bugfix/…`.
- After every release on `main`, **merge `main` into `devel`**.

## Packaging

DEB and RPM packages are built from **`main`** at a release tag.

- **Upstream version** comes from `pyproject.toml` (`[project].version`).
- **Debian revision** (`-1`, `-2`, …) is for packaging-only fixes without bumping
  the upstream version. RPM **Release** is always `1` in the published filename.
- **Build:** `make deb` / `make rpm` / `make packages` (or the scripts under `tools/`;
  see [tools/howto-build-deb.txt](../tools/howto-build-deb.txt) and
  [tools/howto-build-rpm.txt](../tools/howto-build-rpm.txt)).
  Local build artifacts land in `dist/` (gitignored).
- **Target systems:** Debian 12+ or Ubuntu 24.04+ (DEB), Fedora and compatible
  dnf-based systems (RPM), with Python 3.11+.
- **Dependencies:** `[tool.deb]` (`apt_depends`, `pypi_wheelhouse`) and
  `[tool.rpm]` (`dnf_requires`) in `pyproject.toml`. The RPM wheelhouse reuses
  `[tool.deb].pypi_wheelhouse`.
- Push an annotated tag `v` + version (e.g. `v1.0.0a1`). CI
  ([`.github/workflows/release-packages.yml`](../.github/workflows/release-packages.yml))
  builds the `.deb` and `.rpm` and attaches both to the GitHub Release automatically.

## References

- Repository: [mbrennwa/tunes-player](https://github.com/mbrennwa/tunes-player)
- Planning: GitHub issue #10 (versioning / release cycle)
