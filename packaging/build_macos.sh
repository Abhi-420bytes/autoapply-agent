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
if [ -n "${APPLE_SIGN_IDENTITY:-}" ]; then
  # Developer ID signature with the hardened runtime (required for notarization)
  codesign --force --deep --options runtime --timestamp \
    --entitlements "$HERE/entitlements.plist" --sign "$APPLE_SIGN_IDENTITY" "$APP"
else
  codesign --force --deep --sign - "$APP"   # ad-hoc signature (no Apple developer account)
fi

# fixed names so .../releases/latest/download/<file> links never change
ARCH_LABEL="$([[ "$(uname -m)" == "arm64" ]] && echo arm64 || echo intel)"
DMG="$HERE/dist/AutoApply-macos-$ARCH_LABEL.dmg"
TMP="$HERE/dist/dmg"
rm -rf "$TMP" "$DMG" && mkdir -p "$TMP"
cp -R "$APP" "$TMP/"
ln -s /Applications "$TMP/Applications"
hdiutil create -volname "AutoApply" -srcfolder "$TMP" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$TMP"

if [ -n "${APPLE_SIGN_IDENTITY:-}" ] && [ -n "${APPLE_ID:-}" ]; then
  # Notarize with Apple so the download opens without the "could not verify" warning
  codesign --sign "$APPLE_SIGN_IDENTITY" --timestamp "$DMG"
  xcrun notarytool submit "$DMG" --apple-id "$APPLE_ID" --password "$APPLE_APP_PASSWORD" \
    --team-id "$APPLE_TEAM_ID" --wait
  xcrun stapler staple "$DMG"
fi
echo "built: $DMG"
