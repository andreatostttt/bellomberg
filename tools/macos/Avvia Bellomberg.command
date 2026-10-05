#!/bin/bash
# Avvio macOS dal checkout; funziona anche con doppio clic da Finder.
set -euo pipefail

# Il launcher sta in tools/macos/: la radice del checkout e' due livelli sopra.
BELLOMBERG_CHECKOUT="$(cd -- "$(dirname -- "$0")/../.." && pwd -P)"
BELLOMBERG_APP="$BELLOMBERG_CHECKOUT/app"
# Il launcher del checkout usa la sua venv, evitando variabili globali rimaste
# da altri progetti. Per un interprete alternativo usare BELLOMBERG_PYTHON_OVERRIDE.
BELLOMBERG_PYTHON="${BELLOMBERG_PYTHON_OVERRIDE:-$BELLOMBERG_CHECKOUT/.venv/bin/python}"

if [[ ! -d "$BELLOMBERG_APP" ]]; then
  echo "Cartella app assente: $BELLOMBERG_APP" >&2
  exit 1
fi

if [[ ! -x "$BELLOMBERG_PYTHON" ]]; then
  echo "Python virtuale assente: $BELLOMBERG_PYTHON" >&2
  echo "Crea l'ambiente con: python3 -m venv .venv && .venv/bin/python -m pip install -e ." >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  for BELLOMBERG_NODE_BIN in /opt/homebrew/bin /usr/local/bin; do
    if [[ -x "$BELLOMBERG_NODE_BIN/npm" ]]; then
      export PATH="$BELLOMBERG_NODE_BIN:$PATH"
      break
    fi
  done
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "npm non trovato nel PATH. Installa Node.js 22.12+ oppure imposta il PATH nel terminale." >&2
  exit 1
fi

if [[ ! -x "$BELLOMBERG_APP/node_modules/.bin/electron" ]]; then
  echo "Dipendenze desktop assenti: esegui 'npm ci' e 'npm run desktop:install' dentro app/." >&2
  exit 1
fi

export BELLOMBERG_BACKEND_DIR="$BELLOMBERG_CHECKOUT"
export BELLOMBERG_PROJECT_ROOT="$BELLOMBERG_CHECKOUT"
export BELLOMBERG_PYTHON
# Il backend gira dai sorgenti del checkout anche se macOS marca "hidden" il
# .pth dell'install editable (Python 3.14 salta i .pth nascosti e ricadrebbe
# su una copia installata vecchia).
export PYTHONPATH="$BELLOMBERG_CHECKOUT/src${PYTHONPATH:+:$PYTHONPATH}"

cd -- "$BELLOMBERG_APP"
unset ELECTRON_RUN_AS_NODE VITE_DEV_SERVER_URL
exec npm run dev
