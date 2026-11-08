#!/bin/sh
# homelab: write install-stamp.json before `docker build`, the way upstream's CI
# does with scripts/write_install_stamp.py (this builder has git but no Python).
# Without it the image reports "vunknown" and plugins' requires_hermes checks
# compare against an unknown version. Run from the repo root (Komodo pre_build).
#
# The base version comes from the last sync merge on the first-parent line
# ("Merge upstream release X.Y.Z (...)"); distance counts fork commits after it.
set -eu
merge=$(git log --first-parent --grep='^Merge upstream release [0-9]' -1 --format=%H)
base=$(git log -1 --format=%s "$merge" | sed -n 's/^Merge upstream release \([0-9][0-9.]*\).*/\1/p')
[ -n "$base" ] || { echo "homelab-install-stamp: no sync merge found" >&2; exit 1; }
distance=$(git rev-list --count --first-parent "$merge..HEAD")
dirty=false
[ -z "$(git status --porcelain --untracked-files=no)" ] || dirty=true
cat > install-stamp.json <<JSON
{
  "schemaVersion": 2,
  "commit": "$(git rev-parse HEAD)",
  "commitDate": $(git log -1 --format=%ct),
  "branch": "$(git rev-parse --abbrev-ref HEAD)",
  "builtAt": "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)",
  "dirty": $dirty,
  "source": "ci",
  "distribution": "docker",
  "updateMechanism": "external",
  "baseVersion": "$base",
  "displayVersion": "$base",
  "distance": $distance,
  "payload": "bootstrap",
  "tag": null
}
JSON
echo "homelab-install-stamp: $base +$distance @ $(git rev-parse --short HEAD)"
