#!/usr/bin/env bash
# Stage the dashboard (Next.js standalone), Node.js and Tectonic for PyInstaller (macOS/Linux).
# Usage: packaging/prepare.sh   [NODE_BIN=/path/to/node] [TECTONIC_BIN=/path/to/tectonic]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
STAGE="$HERE/stage"
rm -rf "$STAGE" && mkdir -p "$STAGE/node" "$STAGE/bin"

# 1) dashboard: self-contained Next.js server
( cd "$ROOT/frontend" && npm ci --no-audit --no-fund && NEXT_TELEMETRY_DISABLED=1 npm run build )
cp -R "$ROOT/frontend/.next/standalone" "$STAGE/dashboard"
cp -R "$ROOT/frontend/.next/static" "$STAGE/dashboard/.next/static"
cp -R "$ROOT/frontend/public" "$STAGE/dashboard/public"

# 2) Node.js runtime (only this machine's architecture)
NODE_BIN="${NODE_BIN:-$(command -v node)}"
if [[ "$(uname)" == "Darwin" ]] && lipo -info "$NODE_BIN" 2>/dev/null | grep -q "Architectures in the fat file"; then
  lipo -thin "$(uname -m)" "$NODE_BIN" -output "$STAGE/node/node"
else
  cp "$NODE_BIN" "$STAGE/node/node"
fi
chmod +x "$STAGE/node/node"

# 3) Tectonic (LaTeX engine)
TECTONIC_BIN="${TECTONIC_BIN:-$(command -v tectonic || echo "$ROOT/backend/.venv/bin/tectonic")}"
cp "$TECTONIC_BIN" "$STAGE/bin/tectonic" && chmod +x "$STAGE/bin/tectonic"
echo "staged: $(du -sh "$STAGE" | cut -f1)"
