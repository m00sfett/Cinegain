#!/usr/bin/env bash
# Builds a native Debian/Ubuntu .deb package for Cinegain
set -e

VERSION="0.3.0"
ARCH="amd64"
PKG_DIR="/tmp/cinegain-deb-build/cinegain_${VERSION}_${ARCH}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "==> Building Cinegain v${VERSION} Debian Package..."
rm -rf "/tmp/cinegain-deb-build"
mkdir -p "$PKG_DIR/DEBIAN"
mkdir -p "$PKG_DIR/usr/bin"
mkdir -p "$PKG_DIR/usr/lib/ladspa"
mkdir -p "$PKG_DIR/usr/share/cinegain/ui"
mkdir -p "$PKG_DIR/usr/share/cinegain/config"
mkdir -p "$PKG_DIR/usr/share/cinegain/presets"
mkdir -p "$PKG_DIR/usr/share/cinegain/assets/icons"
mkdir -p "$PKG_DIR/usr/share/applications"
mkdir -p "$PKG_DIR/usr/share/icons/hicolor/256x256/apps"
mkdir -p "$PKG_DIR/usr/share/doc/cinegain"

# Compile DSP library
gcc -O3 -fPIC -shared -Wall -Wextra "$SCRIPT_DIR/src/cinegain.c" -o "$PKG_DIR/usr/lib/ladspa/cinegain.so" -lm

# Install files
install -m 755 "$SCRIPT_DIR/bin/cinegain" "$PKG_DIR/usr/bin/cinegain"
install -m 755 "$SCRIPT_DIR/bin/cinegain-gui" "$PKG_DIR/usr/bin/cinegain-gui"

cp -r "$SCRIPT_DIR/ui/"* "$PKG_DIR/usr/share/cinegain/ui/"
cp -r "$SCRIPT_DIR/config/"* "$PKG_DIR/usr/share/cinegain/config/"
cp -r "$SCRIPT_DIR/presets/"* "$PKG_DIR/usr/share/cinegain/presets/"
cp -r "$SCRIPT_DIR/assets/icons/"* "$PKG_DIR/usr/share/cinegain/assets/icons/"

install -m 644 "$SCRIPT_DIR/assets/cinegain.desktop" "$PKG_DIR/usr/share/applications/cinegain.desktop"
install -m 644 "$SCRIPT_DIR/assets/icons/cinegain.png" "$PKG_DIR/usr/share/icons/hicolor/256x256/apps/cinegain.png"
install -m 644 "$SCRIPT_DIR/LICENSE" "$PKG_DIR/usr/share/doc/cinegain/copyright"
install -m 644 "$SCRIPT_DIR/CHANGELOG.md" "$PKG_DIR/usr/share/doc/cinegain/changelog"

# Control metadata
cat << EOF > "$PKG_DIR/DEBIAN/control"
Package: cinegain
Version: ${VERSION}
Section: sound
Priority: optional
Architecture: ${ARCH}
Depends: pipewire (>= 0.3.50), pipewire-pulse | pulseaudio-utils, python3 (>= 3.10), python3-pyqt6
Maintainer: Tobias Moos <tobias@roteson.de>
Description: Realtime volume booster & dialogue clarity optimizer for PipeWire
 Cinegain is a dedicated realtime audio DSP processor designed for movies, TV
 shows, laptops, and stream media on Linux. Features an intelligent dialogue
 leveler, presence boost, center/side isolation, soft-knee compressor, and a
 look-ahead peak limiter that keeps the output below full scale.
EOF

dpkg-deb --build "$PKG_DIR" "$SCRIPT_DIR/packaging/cinegain_${VERSION}_${ARCH}.deb"
echo "✓ Successfully built: $SCRIPT_DIR/packaging/cinegain_${VERSION}_${ARCH}.deb"
