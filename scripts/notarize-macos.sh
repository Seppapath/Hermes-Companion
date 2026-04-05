#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <artifact> [artifact...]" >&2
  exit 1
fi

command -v xcrun >/dev/null 2>&1 || { echo "xcrun is required"; exit 1; }
: "${APPLE_NOTARY_PROFILE:?APPLE_NOTARY_PROFILE is required}"

for artifact in "$@"; do
  if [[ ! -e "$artifact" ]]; then
    echo "artifact not found: $artifact" >&2
    exit 1
  fi

  echo "Submitting $artifact for notarization..."
  xcrun notarytool submit "$artifact" --keychain-profile "$APPLE_NOTARY_PROFILE" --wait
  echo "Stapling $artifact..."
  xcrun stapler staple "$artifact"
done
