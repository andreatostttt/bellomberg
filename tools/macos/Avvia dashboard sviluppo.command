#!/bin/bash
# Frontend isolato, backend esistente: nessun avvio Electron o sostituzione app.
set -euo pipefail
# Il launcher sta in tools/macos/: la radice del checkout e' due livelli sopra.
BELLOMBERG_INTERFACE_ROOT="$(cd -- "$(dirname -- "$0")/../.." && pwd -P)"
if ! command -v npm >/dev/null 2>&1; then
  for BELLOMBERG_INTERFACE_NODE_BIN in /opt/homebrew/bin /usr/local/bin; do
    if [[ -x "$BELLOMBERG_INTERFACE_NODE_BIN/npm" ]]; then
      export PATH="$BELLOMBERG_INTERFACE_NODE_BIN:$PATH"
      break
    fi
  done
fi
cd -- "$BELLOMBERG_INTERFACE_ROOT/app"
exec npm run dev:interface
