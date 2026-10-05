#!/bin/bash
# BELLOMBERG - Auto Price Updater su macOS (launchd, ogni 5 minuti).
# Tiene fresco lo storico prezzi anche ad app chiusa; ad app aperta il
# refresh ogni 60s lo fa gia' l'Electron (stessa catena AUTOMATIC_*).
# Uso: ./install_price_updater.sh [install|uninstall|status|run-once]
set -euo pipefail

LABEL="com.bellomberg.priceupdater"
ROOT="$(cd -- "$(dirname -- "$0")/../../.." && pwd -P)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON="${BELLOMBERG_PYTHON:-/opt/homebrew/bin/python3}"

cmd="${1:-install}"

case "$cmd" in
  install)
    if [[ ! -x "$PYTHON" ]]; then
      echo "Python non trovato: $PYTHON (imposta BELLOMBERG_PYTHON)" >&2
      exit 1
    fi
    mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/data"
    if launchctl list "$LABEL" >/dev/null 2>&1; then
      launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    fi
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$ROOT/price_updater.py</string>
    <string>--no-ibkr</string>
    <string>--quiet</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>BELLOMBERG_PROJECT_ROOT</key><string>$ROOT</string>
    <key>PYTHONPATH</key><string>$ROOT/src</string>
  </dict>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/bellomberg-priceupdater.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/bellomberg-priceupdater.log</string>
</dict>
</plist>
EOF
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "OK: $LABEL ogni 5 min (log: $HOME/Library/Logs/bellomberg-priceupdater.log)"
    ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "OK: $LABEL rimosso"
    ;;
  status)
    launchctl list "$LABEL" 2>&1 || echo "non installato"
    ;;
  run-once)
    cd -- "$ROOT"
    BELLOMBERG_PROJECT_ROOT="$ROOT" \
      "$PYTHON" price_updater.py --no-ibkr --quiet
    ;;
esac
