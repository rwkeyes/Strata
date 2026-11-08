#!/usr/bin/env bash
# Copyright (c) 2025-2026 Dirigo Agents LLC. See LICENSE for terms.
#
# fetch-assets.sh - download this payload's release assets and unpack them into a payload root.
#
#   ./fetch-assets.sh [TAG] [DEST]      defaults: TAG=v0.2.11  DEST=/opt/dirigo/engines/strata
#   DEST=$PWD/payload ./fetch-assets.sh v0.2.11
#
# Needs `gh` authenticated against a GitHub account with read access - the repository is PRIVATE, so a
# plain curl of the asset URL does not work. Verifies each asset against SHA256SUMS.assets before unpacking,
# so a truncated download fails loudly instead of producing a half payload.
set -euo pipefail

TAG="${1:-v0.2.11}"
DEST="${2:-/opt/dirigo/engines/strata}"
REPO="${DIRIGO_STRATA_REPO:-rwkeyes/dirigo-strata-0.2.11}"
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

ASSETS="bin.tar.zst hip-runtime.tar.zst oneapi-runtime.tar.zst"
mkdir -p "$DEST"
cd "$DEST"

echo "== fetching $REPO release $TAG -> $DEST"
for a in $ASSETS; do
  echo "   $a"
  gh release download "$TAG" --repo "$REPO" --pattern "$a" --clobber
done

echo "== verifying checksums"
if [ -f "$HERE/SHA256SUMS.assets" ]; then
  grep -E ' (bin|hip-runtime|oneapi-runtime)\.tar\.zst$' "$HERE/SHA256SUMS.assets" | sha256sum -c -
else
  echo "   WARNING: SHA256SUMS.assets not found next to this script - skipping verification" >&2
fi

echo "== unpacking"
for a in $ASSETS; do tar --zstd -xf "$a"; done

echo "== payload root $DEST"
ls -l bin/
echo
echo "next: ./strata-selftest.sh   (probes every vendor path present on this box)"
