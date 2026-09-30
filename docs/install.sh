#!/bin/bash
# AutoApply one-command installer / updater for macOS.
#
#   curl -fsSL https://abhi-420bytes.github.io/autoapply-agent/install.sh | bash
#
# Downloads the latest AutoApply release for this Mac (Apple Silicon or Intel), installs it
# into /Applications (or ~/Applications if you can't write there), and opens it. Running it
# again updates AutoApply; your data (in ~/Library/Application Support/AutoApply) is kept.
#
# Created by Abhiram (Challa Abhiram). MIT license.
# https://github.com/Abhi-420bytes/autoapply-agent
set -euo pipefail

REPO="Abhi-420bytes/autoapply-agent"
case "$(uname -m)" in
  arm64) FILE="AutoApply-macos-arm64.dmg" ;;
  x86_64) FILE="AutoApply-macos-intel.dmg" ;;
  *) echo "Unsupported Mac architecture: $(uname -m)" >&2; exit 1 ;;
esac
URL="https://github.com/$REPO/releases/latest/download/$FILE"

DEST="${INSTALL_DIR:-/Applications}"
if [ ! -w "$DEST" ]; then
  DEST="$HOME/Applications"
  mkdir -p "$DEST"
fi

echo "==> AutoApply by Abhiram"
echo "==> Downloading $FILE ..."
TMP="$(mktemp -d)"
cleanup() {
  [ -n "${MNT:-}" ] && hdiutil detach "$MNT" -quiet 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT
curl -fL --progress-bar -o "$TMP/$FILE" "$URL"

echo "==> Installing into $DEST ..."
MNT="$(hdiutil attach -nobrowse -readonly -noautoopen "$TMP/$FILE" | awk -F'\t' '/\/Volumes\//{print $NF}' | tail -1)"
[ -d "$MNT/AutoApply.app" ] || { echo "The download didn't contain AutoApply.app" >&2; exit 1; }

# Quit a running AutoApply so it can be replaced (it starts again below).
pkill -f "$DEST/AutoApply.app/Contents/MacOS/AutoApply" 2>/dev/null && sleep 2 || true

rm -rf "$DEST/AutoApply.app"
ditto "$MNT/AutoApply.app" "$DEST/AutoApply.app"
# In case an older copy was downloaded with a browser before: clear its "downloaded" flag.
xattr -dr com.apple.quarantine "$DEST/AutoApply.app" 2>/dev/null || true

if [ -z "${NO_OPEN:-}" ]; then
  echo "==> Opening AutoApply ..."
  open "$DEST/AutoApply.app"
fi
echo "==> Done. AutoApply is in $DEST. Run this command again any time to update."
