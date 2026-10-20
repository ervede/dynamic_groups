#!/usr/bin/env bash
# Update CHANGELOG.md for the version in $VERSION, commit it to main, then tag
# and publish a GitHub release. Run by .github/workflows/release.yml.
#
# Set DRY_RUN=1 to build the changelog and notes locally without pushing or
# creating a release.
set -euo pipefail

tag="v${VERSION}"
repo="${GITHUB_REPOSITORY:-ervede/dynamic_groups}"

previous=$(git tag --list 'v*' --sort=-v:refname | grep -vx "$tag" | head -n 1 || true)

# Commit subjects since the previous release, without changelog bookkeeping.
if [ -n "$previous" ]; then
  range="${previous}..HEAD"
else
  range="HEAD"
fi
git log "$range" --no-merges --pretty='- %s' | grep -v '^- Update changelog' > entries.md || true
if [ ! -s entries.md ]; then
  echo "- Maintenance release" > entries.md
fi

if [ -n "$previous" ]; then
  compare="**Full changelog:** https://github.com/${repo}/compare/${previous}...${tag}"
else
  compare=""
fi

# Release notes
{
  cat entries.md
  if [ -n "$compare" ]; then
    echo
    echo "$compare"
  fi
} > notes.md

# CHANGELOG.md: newest version first, never the same version twice.
[ -f CHANGELOG.md ] || printf '# Changelog\n\n' > CHANGELOG.md
if ! grep -q "^## ${tag} " CHANGELOG.md; then
  {
    head -n 2 CHANGELOG.md
    echo "## ${tag} - $(date -u +%F)"
    echo
    cat entries.md
    echo
    tail -n +3 CHANGELOG.md
  } > CHANGELOG.new
  mv CHANGELOG.new CHANGELOG.md

  if [ "${DRY_RUN:-}" != "1" ]; then
    git config user.name "github-actions[bot]"
    git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
    git add CHANGELOG.md
    git commit -m "Update changelog for ${tag}"
    git push origin HEAD:main
  fi
fi

if [ "${DRY_RUN:-}" = "1" ]; then
  echo "--- DRY RUN: CHANGELOG.md"; cat CHANGELOG.md
  echo "--- DRY RUN: release notes"; cat notes.md
  rm -f entries.md notes.md
  exit 0
fi

# The tag points at the changelog commit, so the release contains its own entry.
gh release create "$tag" \
  --target "$(git rev-parse HEAD)" \
  --title "$tag" \
  --notes-file notes.md \
  --latest
