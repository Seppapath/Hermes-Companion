#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT_DIR/deliveries"
STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE_NAME="${1:-hermes-companion-windows-handoff-$STAMP.zip}"
ARCHIVE_PATH="$OUT_DIR/$ARCHIVE_NAME"
FILE_LIST="$(mktemp)"

cleanup() {
  rm -f "$FILE_LIST"
}
trap cleanup EXIT

mkdir -p "$OUT_DIR"

cd "$ROOT_DIR"

find . \
  \( \
    -path './.git' -o \
    -path './.venv' -o \
    -path './node_modules' -o \
    -path './src-tauri/target' -o \
    -path './.build' -o \
    -path './dist' -o \
    -path './deliveries' \
  \) -prune -o \
  -type f \
  ! -name '*.pyc' \
  ! -name '*.pyo' \
  ! -name '.DS_Store' \
  -print | LC_ALL=C sort > "$FILE_LIST"

rm -f "$ARCHIVE_PATH"
zip -q "$ARCHIVE_PATH" -@ < "$FILE_LIST"

echo "$ARCHIVE_PATH"
