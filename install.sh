#!/usr/bin/env bash
# ==============================================================================
# Cinegain Universal Linux Installer
# Supports local execution or piping directly from GitHub:
#   curl -fsSL https://raw.githubusercontent.com/m00sfett/cinegain/main/install.sh | bash
# ==============================================================================

set -e

VERSION="0.5.3"
REPO_URL="https://github.com/m00sfett/cinegain.git"

# Detect if piped via curl | bash
if [ -z "${BASH_SOURCE[0]}" ] || [ "${BASH_SOURCE[0]}" = "bash" ] || [ "${BASH_SOURCE[0]}" = "-" ]; then
    IS_PIPED=1
else
    IS_PIPED=0
fi

# Determine source root
if [ "$IS_PIPED" -eq 1 ]; then
    TMP_CLONE_DIR=$(mktemp -d /tmp/cinegain-installer.XXXXXX)
    echo "==> Downloading Cinegain v${VERSION} source from GitHub..."
    git clone --depth 1 "$REPO_URL" "$TMP_CLONE_DIR" >/dev/null 2>&1 || {
        echo "Error: Failed to clone repository from $REPO_URL" >&2
        exit 1
    }
    SRC_DIR="$TMP_CLONE_DIR"
    trap 'rm -rf "$TMP_CLONE_DIR"' EXIT
else
    SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

# Parsing arguments
MODE="user"
if [ "$1" = "--system" ] || [ "$EUID" -eq 0 ]; then
    MODE="system"
fi
if [ "$1" = "--uninstall" ]; then
    MODE="uninstall"
fi

# Setup installation paths
if [ "$MODE" = "system" ]; then
    PREFIX="/usr/local"
    BIN_DIR="$PREFIX/bin"
    LIB_DIR="$PREFIX/lib/ladspa"
    SHARE_DIR="$PREFIX/share/cinegain"
    DESKTOP_DIR="/usr/local/share/applications"
    ICON_DIR="/usr/local/share/icons/hicolor/256x256/apps"
else
    PREFIX="$HOME/.local"
    BIN_DIR="$PREFIX/bin"
    LIB_DIR="$PREFIX/lib/ladspa"
    SHARE_DIR="$PREFIX/share/cinegain"
    DESKTOP_DIR="$PREFIX/share/applications"
    ICON_DIR="$PREFIX/share/icons/hicolor/256x256/apps"
fi

if [ "$MODE" = "uninstall" ]; then
    echo "==> Uninstalling Cinegain..."
    rm -f "$BIN_DIR/cinegain" "$BIN_DIR/cinegain-gui"
    rm -f "$LIB_DIR/cinegain.so"
    rm -rf "$SHARE_DIR"
    rm -f "$DESKTOP_DIR/cinegain.desktop"
    rm -f "$ICON_DIR/cinegain.png"
    echo "✓ Cinegain has been completely uninstalled."
    exit 0
fi

echo "======================================================================"
echo " Cinegain v${VERSION} — Film Audio Processor & Volume Booster Installer"
echo " Target prefix: ${PREFIX} (${MODE} mode)"
echo "======================================================================"

# Prerequisite Checks
echo "==> Checking prerequisites..."
MISSING=""

if ! command -v pipewire >/dev/null 2>&1; then
    MISSING="$MISSING pipewire"
fi
if ! command -v pactl >/dev/null 2>&1; then
    MISSING="$MISSING pipewire-pulse/pulseaudio-utils"
fi
if ! command -v python3 >/dev/null 2>&1; then
    MISSING="$MISSING python3"
fi
if ! command -v gcc >/dev/null 2>&1 && ! command -v clang >/dev/null 2>&1; then
    MISSING="$MISSING gcc"
fi

if [ -n "$MISSING" ]; then
    echo "Error: Missing required system dependencies:$MISSING" >&2
    echo "Please install them via your distribution package manager (e.g. pacman, apt, dnf)." >&2
    exit 1
fi

if ! python3 -c "import PyQt6" >/dev/null 2>&1; then
    echo "Notice: PyQt6 not detected in current Python environment."
    echo "        The CLI daemon will work; for the GUI, install python-pyqt6 / python3-pyqt6."
fi

# Compile LADSPA C99 DSP Core
echo "==> Compiling high-performance LADSPA DSP engine (C99)..."
CC="gcc"
if ! command -v gcc >/dev/null 2>&1; then
    CC="clang"
fi
$CC -O3 -fPIC -shared -Wall -Wextra "$SRC_DIR/src/cinegain.c" -o "$SRC_DIR/src/cinegain.so" -lm

# Create directories
mkdir -p "$BIN_DIR"
mkdir -p "$LIB_DIR"
mkdir -p "$SHARE_DIR/ui"
mkdir -p "$SHARE_DIR/config"
mkdir -p "$SHARE_DIR/presets"
mkdir -p "$SHARE_DIR/assets/icons"
mkdir -p "$DESKTOP_DIR"
mkdir -p "$ICON_DIR"

# Install files
echo "==> Installing files..."
install -m 755 "$SRC_DIR/src/cinegain.so" "$LIB_DIR/cinegain.so"
install -m 755 "$SRC_DIR/bin/cinegain" "$BIN_DIR/cinegain"
install -m 755 "$SRC_DIR/bin/cinegain-gui" "$BIN_DIR/cinegain-gui"
install -m 755 "$SRC_DIR/install.sh" "$SHARE_DIR/install.sh"

cp -r "$SRC_DIR/ui/"* "$SHARE_DIR/ui/"
cp -r "$SRC_DIR/config/"* "$SHARE_DIR/config/"
cp -r "$SRC_DIR/presets/"* "$SHARE_DIR/presets/"
cp -r "$SRC_DIR/assets/icons/"* "$SHARE_DIR/assets/icons/"

install -m 644 "$SRC_DIR/assets/cinegain.desktop" "$DESKTOP_DIR/cinegain.desktop"
install -m 644 "$SRC_DIR/assets/icons/cinegain.png" "$ICON_DIR/cinegain.png"

# Update desktop database if available
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi

echo ""
echo "======================================================================"
echo "✓ Installation completed successfully!"
echo "  - CLI command:  $BIN_DIR/cinegain"
echo "  - GUI command:  $BIN_DIR/cinegain-gui"
echo "  - LADSPA plugin: $LIB_DIR/cinegain.so"
echo "  - Desktop App:  Available in application menu under 'Cinegain'"
echo "======================================================================"
echo ""
if [ "$MODE" = "user" ]; then
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *)
            echo "Tip: Make sure ~/.local/bin is in your PATH. Add to ~/.bashrc or ~/.zshrc:"
            echo "     export PATH=\"\$HOME/.local/bin:\$PATH\""
            echo ""
            ;;
    esac
fi
echo "ℹ️  Update Notice:"
echo "   Installations via direct shell command/curl do not update automatically"
echo "   with system package managers. To update Cinegain at any time, simply"
echo "   re-run the installation command:"
echo "   curl -fsSL https://raw.githubusercontent.com/m00sfett/cinegain/main/install.sh | bash"
echo "======================================================================"
