#!/usr/bin/env bash
# Prints the CHANGELOG.md section of one version (without its heading) as release notes.
# Usage: packaging/release-notes.sh 0.5.1 > RELEASE_NOTES.md
# Fails when the section is missing, still marked "Unreleased", or empty.
set -euo pipefail

VERSION="${1:?usage: release-notes.sh <version>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHANGELOG="${ROOT}/CHANGELOG.md"

HEADING="$(grep -m1 -E "^## \[${VERSION//./\\.}\]" "$CHANGELOG" || true)"
if [ -z "$HEADING" ]; then
    echo "release-notes: no section for ${VERSION} in CHANGELOG.md" >&2
    exit 1
fi
if [[ "$HEADING" == *Unreleased* ]]; then
    echo "release-notes: section ${VERSION} is still marked Unreleased; set the release date first" >&2
    exit 1
fi

NOTES="$(awk -v head="$HEADING" '
    $0 == head { inside = 1; next }
    inside && /^## \[/ { exit }
    inside && /^---$/ { exit }
    inside { print }
' "$CHANGELOG" | sed -e '/./,$!d')"

if [ -z "${NOTES//[[:space:]]/}" ]; then
    echo "release-notes: section ${VERSION} is empty" >&2
    exit 1
fi
printf '%s\n' "$NOTES"
