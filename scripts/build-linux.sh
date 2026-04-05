#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_ROOT="$ROOT_DIR/.build/linux"
DIST_DIR="$BUILD_ROOT/dist"
WORK_DIR="$BUILD_ROOT/work"
DAEMON_BIN="$DIST_DIR/hermes-node-daemon"

command -v npm >/dev/null 2>&1 || { echo "npm is required"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required"; exit 1; }
command -v cargo >/dev/null 2>&1 || { echo "cargo is required"; exit 1; }

cd "$ROOT_DIR"

npm install
python3 -m pip install --upgrade pip
python3 -m pip install -r daemon/requirements.txt pyinstaller

rm -rf "$BUILD_ROOT"
mkdir -p "$DIST_DIR" "$WORK_DIR"

python3 -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  daemon/hermes-node-daemon.py \
  --name hermes-node-daemon \
  --distpath "$DIST_DIR" \
  --workpath "$WORK_DIR" \
  --specpath "$WORK_DIR"

chmod +x "$DAEMON_BIN"
node scripts/prepare-resources.mjs "$DAEMON_BIN"

TAURI_CONFIG="$(node scripts/generate-release-config.mjs)"
npm run tauri:build -- --config "$TAURI_CONFIG"

if [[ -n "${HERMES_RELEASE_BASE_URL:-}" ]]; then
  node scripts/generate-release-manifest.mjs
fi
