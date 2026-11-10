# Cinegain

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-red.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![PipeWire](https://img.shields.io/badge/Audio-PipeWire-blue.svg)](https://pipewire.org)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-yellow.svg)](https://www.python.org/)
[![GUI: PyQt6](https://img.shields.io/badge/GUI-PyQt6-green.svg)](https://riverbankcomputing.com/software/pyqt/)
[![Version](https://img.shields.io/badge/Release-v0.3.3-orange.svg)](https://github.com/m00sfett/cinegain/releases)

> **Realtime Volume Booster, Dialogue Clarity Optimizer & Film Audio Processor for Linux (PipeWire).**

Cinegain solves the classic movie audio problem on Linux: **quiet, mumbled dialogues drowned out by deafening explosions and loud background music**. It also serves as a clean **system volume booster** capable of pushing media audio up to **+25 dB** while keeping the output below full scale (0 dBFS). Pushed into the limiter, material is of course compressed, not transparent.

Unlike simple equalizers or aggressive compressors that distort the entire mix, Cinegain runs a dedicated low-latency C99 LADSPA DSP filter chain directly inside PipeWire. It favours the center (mid) channel where dialogue usually sits, slowly lifts quiet speech, tames loud peaks with a compressor, and caps them with a look-ahead peak limiter.

---

## Key Features

- **Direct Software Volume Booster (+25 dB):**
  Instantly amplifies quiet movie files, streams, or low-volume laptop speakers up to +25 dB.
- **Intelligent Dialogue Leveler:**
  A slow RMS-based leveler (no look-ahead, glide time set by the preset) raises quiet passages up to a target level. Loud scenes are handled by the compressor and the limiter; the leveler itself needs seconds to react.
- **Acoustic Presence Filter (2.5 kHz):**
  Selectively boosts the core consonant frequencies of human speech without making the audio sound harsh or tinny.
- **Mid/Side Acoustic Separation:**
  Balances the mid (center) against the side (stereo width) signal with separate gains. This favours centered dialogue over wide effects and music, but it is a level balance, not a true separation.
- **Look-Ahead Peak Limiter:**
  A 5 ms look-ahead on sample peaks pulls the output down to the configured ceiling (default -0.5 dBFS) before the peak arrives. Measured overshoot above the ceiling stays around 0.2 dB. A soft safety clip with a hard stop keeps the output at or below 0 dBFS, even when pushed to maximum gain. The limiter works on sample peaks only and does not detect inter-sample (true-peak) overs; with hard-clipped broadband material at +25 dB the true peak can sit several dB above the ceiling.
- **Red-Yellow-Black Desktop Interface:**
  Modern two-column layout with high-contrast hero sliders and system tray integration. There is no level or gain-reduction meter.
- **Full Keyboard Navigation:**
  Control every parameter using the keyboard (`↑`/`↓` to switch sliders, `←`/`→` for 1.0 dB steps, `Shift + ←`/`→` for 0.1 dB fine tuning).
- **8 User Presets:**
  Store and recall your own custom audio profiles. Click an empty slot to save, right-click to overwrite, rename or clear; the active slot is highlighted while the sliders match it. Later slider changes are not written back automatically.
- **Settings per Program, Remembered Permanently:**
  Every program that ever played audio stays in the sidebar (`~/.config/cinegain/apps.json`). Select a program and the presets and sliders edit exactly that program; untouched programs follow *Standard*. A program with its own setting runs through its own filter sink, so a film in the browser and music in Spotify play at the same time with different processing. A switch per program decides whether it goes through Cinegain at all. Electron apps that pretend to be "Chromium" (e.g. the ChatGPT desktop client) are told apart by their binary.
- **Reset Everything:**
  One button (or `cinegain reset`) returns sliders, user presets, program list and language to factory settings. The previous state is backed up to `~/.local/share/cinegain/backups/` first.
- **Multi-Language Support (19 Languages):**
  Instant live translation switcher in the title bar (Material Translate `A / 文` icon): English, German, Spanish, French, Italian, Portuguese, Dutch, Polish, Turkish, Russian, Ukrainian, Arabic, Hindi, Indonesian, Vietnamese, Japanese, Korean, Simplified Chinese, Traditional Chinese.
- **Headless Daemon & CLI:**
  Full command-line interface (`cinegain start`, `stop`, `status`, `set`, `preset`, `route`, `watch`, `apps`, `templates`, `reset`) for scripting or headless desktop environments.

---

## Requirements

### Runtime Requirements
All installation methods require:
- **Linux OS** with **PipeWire** (>= 0.3.50) and `pipewire-pulse` (default audio stack on Arch, CachyOS, Fedora 34+, Ubuntu 22.10+, Debian 12+, openSUSE).
- **Python** (>= 3.10).
- **PyQt6** (`python-pyqt6` / `python3-pyqt6`) for the graphical desktop interface. *(The headless CLI works without PyQt6).*
- Standard C math library (`libm`).

### Build Requirements
*(Only required if installing via the curl one-liner, AUR/PKGBUILD, or compiling from source. The pre-built `.deb` package does **not** require any build tools.)*
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
sudo apt install ./cinegain_0.3.3_amd64.deb
```

*Or build the `.deb` package yourself:*
```bash
./packaging/debian/build-deb.sh
sudo apt install ./packaging/cinegain_0.3.3_amd64.deb
```

> [!NOTE]
> **Update Mechanism:** Standalone `.deb` packages must be updated by downloading and installing newer `.deb` release files.

---

### Method 4: Fedora / RHEL (build the .rpm yourself)

There is no prebuilt `.rpm` on the Releases page (only the tarball and the `.deb`). The spec file is `packaging/rpm/cinegain.spec`; build it with `rpmbuild`, or use Method 5 (tarball) or Method 1.

---

### Method 5: Standalone Release Archive (Universal Tarball)

Download `cinegain-0.3.3-linux-x86_64.tar.gz` from [GitHub Releases](https://github.com/m00sfett/cinegain/releases):

```bash
tar -xzvf cinegain-0.3.3-linux-x86_64.tar.gz
cd cinegain-0.3.3-linux-x86_64
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
| **Fedora (`.rpm`)** | ❌ No (build from spec) | Rebuild with `rpmbuild` from a newer source |

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
- **Programs (sidebar):** Click *Standard* or a program; the header above the presets names what you are editing. The first slider move or preset gives a program its own setting, *Back to Standard* discards it. The switch in each row routes the program through Cinegain or straight to the speakers. Programs silent for more than a week fold away under *Silent for a while*. Right-click a program to rename it, set its own volume back to 100 % or forget it. A yellow volume badge (e.g. `40 %`) means the program itself is turned down; one click fixes it.
- **Presets & user slots are templates:** A click loads the values into the selected program (or Standard). Right-click a user slot to store the current values in it, rename or clear it.
- **Keyboard:** `Page Up` / `Page Down` switch the program; arrows work as before.
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

# Programs and their settings:
cinegain apps                          # list every program ever heard
cinegain apps load spotify night_mode  # Spotify gets its own setting from the Night Mode preset
cinegain apps standard spotify         # Spotify follows Standard again
cinegain apps off chatgpt              # ChatGPT bypasses Cinegain
cinegain apps on chatgpt
cinegain apps rename chatgpt "ChatGPT Voice"
cinegain apps forget mpv
cinegain templates                     # presets and user slots you can load

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
 [ Input Gain (-25 .. +25 dB in the DSP, -12 .. +25 dB in the GUI) ]
       │
       ▼
 [ High-Pass (default 20 Hz) ] ─► [ 120 Hz Bass Shelf ] ─► [ Presence Peaking EQ (default 2.5 kHz, adjustable) ] ─► [ 8 kHz Treble Shelf ]
       │
       ▼
 [ Mid/Side Matrix ]
  ├── Mid  (center)  ──► gain (DSP -15 .. +15 dB, GUI -6 .. +12 dB)
  └── Side (width)   ──► gain (DSP -30 .. +15 dB, GUI -15 .. +6 dB)
       │
       ▼
 [ Mid/Side Matrix back to L / R ]
       │
       ▼
 [ Slow RMS Leveler (gated, no look-ahead, boost and cut limits) ]
       │
       ▼
 [ Soft-Knee Feed-Forward Compressor + Makeup Gain ]
       │
       ▼
 [ 5 ms Look-Ahead Peak Limiter + safety soft clip / hard stop at 0 dBFS ]
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
