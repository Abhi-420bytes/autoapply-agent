#!/usr/bin/env bash
# Build AutoApply.app and AutoApply-<version>-macos-<arch>.dmg (no Docker needed to run it).
# Usage: packaging/build_macos.sh [version]      Needs: node/npm, the backend venv (or PYTHON).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
VERSION="${1:-${AUTOAPPLY_VERSION:-1.0.0}}"
PYTHON="${PYTHON:-$ROOT/backend/.venv/bin/python}"
export AUTOAPPLY_VERSION="$VERSION"

"$HERE/prepare.sh"
rm -rf "$HERE/build" "$HERE/dist"
"$PYTHON" -m PyInstaller --noconfirm --clean --distpath "$HERE/dist" --workpath "$HERE/build" "$HERE/autoapply.spec"

APP="$HERE/dist/AutoApply.app"
codesign --force --deep --sign - "$APP"   # ad-hoc signature (no Apple developer account)

DMG="$HERE/dist/AutoApply-$VERSION-macos-$(uname -m).dmg"
TMP="$HERE/dist/dmg"
rm -rf "$TMP" "$DMG" && mkdir -p "$TMP"
cp -R "$APP" "$TMP/"
ln -s /Applications "$TMP/Applications"
hdiutil create -volname "AutoApply" -srcfolder "$TMP" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$TMP"
echo "built: $DMG"
