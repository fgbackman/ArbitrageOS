#!/bin/bash
# Double-click in Finder to start ArbitrageOS and open the dashboard.
cd "$(dirname "$0")" || exit 1
PY="$(command -v python3)"
if [ -z "$PY" ]; then
  echo "Python 3 is missing. Run: xcode-select --install   then double-click this again."
  read -r -p "Press Return to close."
  exit 1
fi
if lsof -iTCP:8787 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "ArbitrageOS is already running."
  open "http://127.0.0.1:8787/"
  exit 0
fi
if [ ! -f data/arbos.db ]; then
  echo "First run: loading demo data so every screen has something in it."
  "$PY" -m arbos demo >/dev/null
fi
"$PY" -m arbos serve --open
