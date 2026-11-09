#!/bin/bash
set -e

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../src" && pwd)"
BIN_TARGET="$SRC_DIR/cinegain.so"

echo "Kompiliere Cinegain LADSPA Plugin..."
gcc -O3 -fPIC -shared -Wall -Wextra "$SRC_DIR/cinegain.c" -o "$BIN_TARGET" -lm
echo "Erfolgreich erstellt: $BIN_TARGET"
