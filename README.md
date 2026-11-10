# Cinegain

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-red.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![PipeWire](https://img.shields.io/badge/Audio-PipeWire-blue.svg)](https://pipewire.org)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-yellow.svg)](https://www.python.org/)
[![GUI: PyQt6](https://img.shields.io/badge/GUI-PyQt6-green.svg)](https://riverbankcomputing.com/software/pyqt/)
[![Version](https://img.shields.io/badge/Release-v0.2.0-orange.svg)](https://github.com/m00sfett/cinegain/releases)

> **Realtime Volume Booster, Dialogue Clarity Optimizer & Film Audio Processor for Linux (PipeWire).**

Cinegain solves the classic movie audio problem on Linux: **quiet, mumbled dialogues drowned out by deafening explosions and loud background music**. It also serves as a clean, distortion-free **system volume booster** capable of pushing media audio up to **+25 dB** while keeping the output below full scale (0 dBFS).

Unlike simple equalizers or aggressive compressors that distort the entire mix, Cinegain runs a dedicated low-latency C99 LADSPA DSP filter chain directly inside PipeWire. It isolates the center speech channel, elevates quiet whispers, softens harsh transients, and caps peaks with a look-ahead peak limiter.

---

## Key Features

- **Direct Software Volume Booster (+25 dB):**
  Instantly amplifies quiet movie files, streams, or low-volume laptop speakers up to +25 dB.
- **Intelligent Dialogue Leveler:**
  Continuously monitors RMS dialogue levels with peak look-ahead, boosting quiet speech passages while gracefully pulling back during loud action scenes.
- **Acoustic Presence Filter (2.5 kHz):**
  Selectively boosts the core consonant frequencies of human speech without making the audio sound harsh or tinny.
- **Mid/Side Acoustic Separation:**
  Separates center dialogue from stereo sound effects, music, and room reverberation.
- **Look-Ahead Peak Limiter:**
  A 5 ms look-ahead on sample peaks pulls the output down to the configured ceiling (default -0.5 dBFS) before the peak arrives. Measured overshoot above the ceiling stays around 0.2 dB. A soft safety clip with a hard stop keeps the output at or below 0 dBFS, even when pushed to maximum gain. The limiter works on sample peaks only and does not detect inter-sample (true-peak) overs.
- **Red-Yellow-Black Desktop Interface:**
  Modern two-column layout with high-contrast hero sliders, real-time feedback, and system tray integration.
- **Full Keyboard Navigation:**
  Control every parameter using the keyboard (`↑`/`↓` to switch sliders, `←`/`→` for 1.0 dB steps, `Shift + ←`/`→` for 0.1 dB fine tuning).
- **8 Auto-Saving User Presets:**
  Store and recall your own custom audio profiles with live change indicators and slot renaming.
- **Per-Program Profiles, Remembered Permanently:**
  Every program that ever played audio stays in the list, with its choice saved in `~/.config/cinegain/apps.json`. Pick a profile per program: *Off* (direct to the speakers), *Standard* (the main sliders) or any factory preset or user slot. Programs on different profiles are processed by separate filter sinks, so a film in the browser and music in Spotify can play at the same time with different settings. Electron apps that pretend to be "Chromium" (e.g. the ChatGPT desktop client) are told apart by their binary.
- **Reset Everything:**
  One button (or `cinegain reset`) returns sliders, user presets, program list and language to factory settings. The previous state is backed up to `~/.local/share/cinegain/backups/` first.
- **Multi-Language Support (19 Languages):**
  Instant live translation switcher in the title bar (Material Translate `A / 文` icon): English, German, Spanish, French, Italian, Portuguese, Dutch, Polish, Turkish, Russian, Ukrainian, Arabic, Hindi, Indonesian, Vietnamese, Japanese, Korean, Simplified Chinese, Traditional Chinese.
- **Headless Daemon & CLI:**
  Full command-line interface (`cinegain start`, `stop`, `status`, `set`, `preset`, `route`, `watch`, `apps`, `profiles`, `reset`) for scripting or headless desktop environments.

---

## Requirements

### Runtime Requirements
All installation methods require:
- **Linux OS** with **PipeWire** (>= 0.3.50) and `pipewire-pulse` (default audio stack on Arch, CachyOS, Fedora 34+, Ubuntu 22.10+, Debian 12+, openSUSE).
- **Python** (>= 3.10).
- **PyQt6** (`python-pyqt6` / `python3-pyqt6`) for the graphical desktop interface. *(The headless CLI works without PyQt6).*
- Standard C math library (`libm`).

### Build Requirements
*(Only required if installing via the curl one-liner, AUR/PKGBUILD, or compiling from source. Pre-built `.deb` and `.rpm` packages do **not** require any build tools.)*
- C99-compliant compiler (`gcc` or `clang`).
- Standard development tools (`make`, `git`, `curl`).

---

## Installation Methods

### Method 1: Instant One-Liner (Recommended for Quick Setup)

Run this single command in your terminal to automatically compile and install Cinegain for your user (no `sudo` required):

```bash
curl -fsSL https://raw.githubusercontent.com/m00sfett/cinegain/main/install.sh | bash
```

> [!IMPORTANT]
> **Update Notice for Direct Shell / Curl Installations:**
> Installations performed via direct shell scripts do **not** receive automatic background updates from your Linux distribution package manager. To update Cinegain to the latest release in the future, simply re-run the same `curl ... | bash` command, or upgrade to a package-managed installation (such as AUR, DEB, or RPM).

---

### Method 2: Arch Linux / CachyOS (AUR / PKGBUILD)

For Arch-based distributions, Cinegain is built natively from source using the provided `PKGBUILD`:

```bash
# Clone and build locally:
git clone https://github.com/m00sfett/cinegain.git
cd cinegain/packaging/arch
makepkg -si
```

*Or via your preferred AUR helper once published:*
```bash
paru -S cinegain
# or: yay -S cinegain
```

> [!TIP]
> **Update Mechanism:** AUR packages are automatically updated alongside regular system updates (`paru -Syu` / `yay -Syu`).

---

### Method 3: Debian / Ubuntu (.deb Package)

Download the `.deb` package from the [Releases page](https://github.com/m00sfett/cinegain/releases) and install via `apt`:

```bash
sudo apt update
sudo apt install ./cinegain_0.2.0_amd64.deb
```

*Or build the `.deb` package yourself:*
```bash
./packaging/debian/build-deb.sh
sudo apt install ./packaging/cinegain_0.2.0_amd64.deb
```

> [!NOTE]
> **Update Mechanism:** Standalone `.deb` packages must be updated by downloading and installing newer `.deb` release files.

---

### Method 4: Fedora / RHEL (.rpm Package)

Download the `.rpm` package from the [Releases page](https://github.com/m00sfett/cinegain/releases) or build it using the spec file:

```bash
sudo dnf install ./cinegain-0.2.0-1.x86_64.rpm
```

---

### Method 5: Standalone Release Archive (Universal Tarball)

Download `cinegain-0.2.0-linux-x86_64.tar.gz` from [GitHub Releases](https://github.com/m00sfett/cinegain/releases):

```bash
tar -xzvf cinegain-0.2.0-linux-x86_64.tar.gz
cd cinegain-0.2.0-linux-x86_64
./install.sh        # Installs for current user (~/.local)
# or: sudo ./install.sh --system  # Installs system-wide (/usr/local)
```

---

### Method 6: Build from Source

```bash
git clone https://github.com/m00sfett/cinegain.git
cd cinegain
gcc -O3 -fPIC -shared -Wall -Wextra src/cinegain.c -o src/cinegain.so -lm
./bin/cinegain-gui
```

---

## Update Mechanism Summary

| Installation Path | Automatic Updates via System? | How to Update |
|---|---|---|
| **AUR (`paru` / `yay`)** | ✅ Yes | Automatic during `paru -Syu` |
| **Direct One-Liner (`curl | bash`)** | ❌ No | Re-run `curl -fsSL ... | bash` |
| **Standalone Tarball** | ❌ No | Download newer archive and run `./install.sh` |
| **Debian / Ubuntu (`.deb`)** | ❌ No (standalone package) | Download newer `.deb` from GitHub Releases |
| **Fedora (`.rpm`)** | ❌ No (standalone package) | Download newer `.rpm` from GitHub Releases |

---

## Quick Start & Usage

### 1. Graphical Interface (GUI)
Start the interface from your desktop application launcher or terminal:
```bash
cinegain-gui
```

- **Filter Toggle:** Click the large red/green button in the banner to toggle Cinegain on or off without altering your slider positions.
- **Language Switcher:** Click the `🌐` / `A / 文` button in the title banner to instantly switch between 19 languages.
- **Factory Presets:** Click any preset button (`+5 dB`, `+10 dB`, `+15 dB`, `+20 dB`, `+25 dB`, `Film & Serie`, `Night Mode`, `Neutral 0 dB`).
- **User Slots:** Click any empty slot (`User 1`–`8`) to save the current slider state. Left-click to recall; right-click to rename or clear.
- **Keyboard Controls:** Use `↑` / `↓` to select sliders. Press `←` / `→` for 1.0 dB steps, or hold `Shift` for fine 0.1 dB steps.
- **Programs & Profiles:** Choose a profile per program from its drop-down. Right-click a program to rename it, set its own volume back to 100 % or forget it. A yellow `🔉 40 %` badge means the program itself is turned down; one click fixes it.
- **Reset everything:** The button in the bottom left asks for confirmation, backs up the current state and restarts the window in factory state.

### 2. Command-Line Interface (CLI)
Cinegain provides a standalone daemon controller:

```bash
# Start the filter chain in the background and auto-route media streams:
cinegain start --route

# Check current status, node ID, and live parameter values:
cinegain status

# Output status as structured JSON:
cinegain status --json

# Adjust parameters live (works while filter is active or inactive):
cinegain set input_db 12.0
cinegain set dialog_db 5.0
cinegain set lev_max_boost_db 18.0

# Apply a preset via CLI:
cinegain preset dialog_boost
cinegain preset boost_10db
cinegain preset clean_linear

# Apply per-program routing once, or keep it applied without the GUI:
cinegain route
cinegain watch

# Programs and their profiles:
cinegain apps                         # list every program ever heard
cinegain apps set spotify night_mode  # Spotify gets its own Night Mode sink
cinegain apps set chatgpt off         # ChatGPT bypasses Cinegain
cinegain apps rename chatgpt "ChatGPT Voice"
cinegain apps forget mpv
cinegain profiles                     # list usable profiles

# Reset everything to factory settings (state is backed up first):
cinegain reset

# Stop the filter chain and release all audio streams:
cinegain stop
```

---

## DSP Architecture

```
Audio Input (L / R)
       │
       ▼
 [ Direct Volume Boost Stage (-12 dB .. +25 dB) ]
       │
       ▼
 [ 120 Hz Sub-Bass Shelf EQ ] ───► [ 8 kHz High-Shelf Brilliance EQ ]
       │
       ▼
 [ 2.5 kHz Speech Presence Peaking Bandpass ]
       │
       ▼
 [ Mid/Side Matrix Demux ]
  ├── Mid  (Center Dialogue) ──► Dialogue Isolation Gain (+0 .. +12 dB)
  └── Side (Ambience / Stereo) ──► Ambience Attenuation (-15 .. +6 dB)
       │
       ▼
 [ Mid/Side Matrix Mux ]
       │
       ▼
 [ Peak-Sensing Dialogue Leveler (Look-Ahead, Smooth RMS Ballistics) ]
       │
       ▼
 [ Soft-Knee Feedforward Compressor (Threshold, Ratio, Makeup Gain) ]
       │
       ▼
 [ Look-Ahead Peak Limiter (-0.5 dBFS default ceiling) ]
       │
       ▼
 Audio Output (L / R to Speakers / Headphones)
```

---

## Uninstallation

To completely remove Cinegain and its configuration:

```bash
# If installed in user space:
~/.local/share/cinegain/install.sh --uninstall
# Or from the cloned source directory:
./install.sh --uninstall
```

---

## License & Credits

- **License:** [GNU Affero General Public License v3.0 (AGPLv3)](file:///home/tobias/agent/projects/cinegain/code/v1/LICENSE)
- **Author:** Tobias Moos ([roteson.de](https://roteson.de))
