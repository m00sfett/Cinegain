# Cinegain

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-red.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![PipeWire](https://img.shields.io/badge/Audio-PipeWire-blue.svg)](https://pipewire.org)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-yellow.svg)](https://www.python.org/)
[![GUI: PyQt6](https://img.shields.io/badge/GUI-PyQt6-green.svg)](https://riverbankcomputing.com/software/pyqt/)
[![Version](https://img.shields.io/badge/Release-v0.5.3-orange.svg)](https://github.com/m00sfett/cinegain/releases)

> **Louder than your system allows — and finally a look at the volumes PipeWire hides from you. For Linux (PipeWire).**

Cinegain has two main features:

1. **Volume above the system limit (the hero feature).** Any program — browser, video player, a read-aloud voice — gets louder than 100 % system volume, cleanly, with a look-ahead limiter that keeps the output below full scale. The scale runs from **−30 dB to +15 dB**; **overdrive** widens the top to **+40 dB** after an explicit warning. Raising the volume always glides up at 10 dB/s, so a slip of the mouse never blasts you; turning it down is instant.
2. **Hidden volumes: see, set, reset.** PipeWire keeps a second volume per stream that no mixer shows, and WirePlumber restores it for every new stream. On the author's machine Chrome sat at −48 dB and Electron apps at −84 dB while every mixer showed 100 %. Cinegain's *Analysis* view lists every playback node with its hidden volume, the mixer volume and WirePlumber's saved value, lets you set or reset each one and keeps a 7-day timeline of every lowering.

Everything runs in a small C99 LADSPA plugin inside PipeWire, per program if you like. The film-sound chain (leveler, compressor, speech focus) is still there as *Dynamic · Beta*. Details: [docs/SPEC.md](docs/SPEC.md).

---

## Key Features

- **Volume first — −30 to +15 dB, overdrive to +40 dB:**
  The hero slider sits on top. `−`/`+` buttons next to every slider step 1 dB (Shift 0.1 dB, Ctrl 5 dB, hold to repeat); the same steps work with the arrow keys, the mouse wheel and clicks into the track. Right-click a slider to reset it. Quick steps `0`, `+3`, `+6`, `+9`, `+12`, `+15 dB`. Overdrive is switched on in the title bar after a warning with a confirmation box; it lasts until Cinegain restarts and switching it off drops everything above +15 dB at once.
- **Gentle rise, instant fall:**
  Every gain inside the plugin rises at most 10 dB/s and falls immediately — for slider moves, presets, the tray, the CLI, a filter that starts or is switched back on. The hero slider shows how many dB are still to go.
- **Hidden-volume analysis:**
  Table of all playback streams, Cinegain's own nodes and (read-only) the sound cards: hidden volume, mixer volume, mute, WirePlumber's saved value; set 0–100 % or reset per row; timeline of the last 7 days with who did what. Automatic fade-back for programs routed through Cinegain can be switched off to watch lowerings happen. *Full volume reset* (button, tray or `cinegain volume reset`) puts hidden volumes back to 0 dB, normal volumes to 100 %, unmutes and cleans the saved values, lowering Cinegain's own gain first so nothing blasts.
- **Three view levels:**
  *Simple* shows only the volume. *Advanced* adds all sliders, presets and slots, the hidden volume and the analysis view, and *Dynamic · Beta*. *Pro* adds raw values (node IDs, sinks, linear values) and the debug log inside the app. The level is remembered.
- **Two modes per program — *Fixed* and *Dynamic · Beta*:**
  *Fixed* is a plain volume boost with a fixed tone (voices, bass, treble, stereo width). *Dynamic · Beta* is the film-sound chain below. Beta features wear a dashed outline. Each mode keeps its own values.
- **Intelligent Dialogue Leveler:**
  Continuously monitors RMS dialogue levels with peak look-ahead, boosting quiet speech passages while gracefully pulling back during loud action scenes.
- **Acoustic Presence Filter (2.5 kHz):**
  Selectively boosts the core consonant frequencies of human speech without making the audio sound harsh or tinny.
- **Mid/Side Acoustic Separation:**
  Separates center dialogue from stereo sound effects, music, and room reverberation.
- **Look-Ahead Peak Limiter:**
  A 5 ms look-ahead on sample peaks pulls the output down to the configured ceiling (default -0.5 dBFS) before the peak arrives. Measured overshoot above the ceiling stays around 0.2 dB. A soft safety clip with a hard stop keeps the output at or below 0 dBFS, even when pushed to maximum gain. The limiter works on sample peaks only and does not detect inter-sample (true-peak) overs.
- **Red-Yellow-Black Desktop Interface:**
  Two-column layout with a high-contrast hero slider, real-time meters, a resizable program list and system tray integration.
- **Full Keyboard Navigation:**
  Control every parameter using the keyboard (`↑`/`↓` to switch sliders, `←`/`→` for 1 dB, `Shift` 0.1 dB, `Ctrl` 5 dB, `F6` other column, `Page Up`/`Page Down` program).
- **6 User Slots:**
  Store and recall your own settings, including their mode, with live match indicators and slot renaming.
- **Settings per Program, Remembered Permanently:**
  Every program that ever played audio stays in the sidebar (`~/.config/cinegain/apps.json`). Select a program and the presets and sliders edit exactly that program; untouched programs follow *Standard*. A program with its own setting runs through its own filter sink, so a film in the browser and music in Spotify play at the same time with different processing. A switch per program decides whether it goes through Cinegain at all. Electron apps that pretend to be "Chromium" (e.g. the ChatGPT desktop client) are told apart by their binary.
- **Reset Everything:**
  One button (or `cinegain reset`) returns sliders, user presets, program list and language to factory settings. The previous state is backed up to `~/.local/share/cinegain/backups/` first.
- **Multi-Language Support (19 Languages):**
  Instant live translation switcher in the title bar (Material Translate `A / 文` icon): English, German, Spanish, French, Italian, Portuguese, Dutch, Polish, Turkish, Russian, Ukrainian, Arabic, Hindi, Indonesian, Vietnamese, Japanese, Korean, Simplified Chinese, Traditional Chinese.
- **Headless Daemon & CLI:**
  Full command-line interface (`cinegain start`, `stop`, `status`, `set`, `batch`, `mode`, `preset`, `route`, `watch`, `apps`, `templates`, `volume`, `overdrive`, `log`, `reset`) for scripting or headless desktop environments.

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

> [!NOTE]
> **Verify downloads:** From v0.5.1 on, every release ships a `SHA256SUMS` file. Put it next to the downloaded files and run `sha256sum -c SHA256SUMS --ignore-missing`.

---

### Method 3: Debian / Ubuntu (.deb Package)

Download the `.deb` package from the [Releases page](https://github.com/m00sfett/cinegain/releases) and install via `apt`:

```bash
sudo apt update
sudo apt install ./cinegain_0.5.3_amd64.deb
```

*Or build the `.deb` package yourself:*
```bash
./packaging/debian/build-deb.sh
sudo apt install ./packaging/cinegain_0.5.3_amd64.deb
```

> [!NOTE]
> **Update Mechanism:** Standalone `.deb` packages must be updated by downloading and installing newer `.deb` release files.

---

### Method 4: Fedora / RHEL (.rpm Package)

Download the `.rpm` package from the [Releases page](https://github.com/m00sfett/cinegain/releases) or build it using the spec file:

```bash
sudo dnf install ./cinegain-0.5.3-1.x86_64.rpm
```

---

### Method 5: Standalone Release Archive (Universal Tarball)

Download `cinegain-0.5.3-linux-x86_64.tar.gz` from [GitHub Releases](https://github.com/m00sfett/cinegain/releases):

```bash
tar -xzvf cinegain-0.5.3-linux-x86_64.tar.gz
cd cinegain-0.5.3-linux-x86_64
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

- **View level:** *Simple | Advanced | Pro* in the title bar. Simple shows only the volume; Advanced adds every slider, presets, slots, hidden volumes, the *Analysis* tab and *Dynamic · Beta*; Pro adds raw values and the *Log* tab.
- **Volume:** the hero slider (−30 to +15 dB). `−`/`+` next to every slider: 1 dB, `Shift` 0.1 dB, `Ctrl` 5 dB, hold to repeat. Right-click a slider to reset it to neutral. Raising glides at 10 dB/s (the slider shows what is still to go), lowering is instant.
- **Overdrive:** the orange button in the title bar. A warning with a confirmation box comes first; then the volume (and the dynamic make-up gain) reach +40 dB, the zone above +15 dB is marked orange. Off — or a restart — drops everything above +15 dB at once.
- **Filter Toggle:** the large button in the banner switches every filter to pass-through without moving streams, so browsers do not pause.
- **Language Switcher:** the `A / 文` button switches between 19 languages.
- **Mode switch:** *Fixed | Dynamic · Beta* in the header next to the program name (Advanced and Pro). Dynamic adds leveler, center, compressor, make-up and limiter below the five shared sliders. Beta features have a dashed outline.
- **Presets:** volume steps `Neutral 0 dB`, `+3`…`+15 dB` (Fixed); film presets `Film & Serie`, `Night Mode`, `Max Cinema` (Dynamic · Beta, dashed).
- **User Slots:** six slots. Click an empty slot to save the current mode and values, left-click to load, right-click to rename, overwrite or clear.
- **Hidden volume:** the header always shows the hidden volume of the selected target (0 dB when nothing is lowered). A red `-48 dB` badge on a program and a bar under the banner appear when PipeWire turns a stream down. The *Analysis* tab shows every node, lets you set or reset it and keeps a 7-day timeline; *Reset automatically* there decides whether Cinegain fades routed programs back by itself.
- **Programs (sidebar):** drag the handle between list and editor to make it wider (double-click resets to 280 px). Click *Standard* or a program; the first change gives a program its own setting, *Back to Standard* discards it. The switch routes a program through Cinegain or straight to the speakers. Right-click a program to rename it, set its own volume back to 100 % or forget it.
- **Keyboard:** `↑`/`↓` select sliders, `←`/`→` change them (`Shift` 0.1 dB, `Ctrl` 5 dB), `F6` jumps to the other column, `Page Up`/`Page Down` switch the program.
- **Reset everything:** the button in the bottom left asks for confirmation, backs up the current state and restarts the window in factory state.

### 2. Command-Line Interface (CLI)
Cinegain provides a standalone daemon controller:

```bash
# Start the filter chain in the background and auto-route media streams:
cinegain start --route

# Check current status, node ID, and live parameter values:
cinegain status

# Output status as structured JSON:
cinegain status --json

# Adjust parameters live (works while filter is active or inactive);
# the volume range is -30..+15 dB, up to +40 dB with overdrive:
cinegain set input_db 12.0
cinegain set dialog_db 5.0
cinegain set lev_max_boost_db 18.0

# Apply a preset via CLI:
cinegain preset dialog_boost
cinegain preset boost_9db
cinegain preset clean_linear

# Apply per-program routing once, or keep it applied without the GUI
# (watch refuses to start while the GUI runs: only one process may move streams):
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
cinegain mode                          # show Standard's mode
cinegain mode dynamic                  # Standard: dynamic (beta)
cinegain mode fixed --app chrome       # Chrome: fixed (gets its own setting)
cinegain batch '{"input_db": 12}' --mode fixed

# Hidden PipeWire volumes: list, set (0-100 %), reset (volume-reset still works):
cinegain volume                        # every playback node: hidden, mixer and saved volume
cinegain volume list --json
cinegain volume set chromium 100
cinegain volume reset                  # full volume reset
cinegain volume reset chatgpt          # only one program

# Overdrive (volume up to +40 dB until Cinegain restarts):
cinegain overdrive                     # show
cinegain overdrive on --confirm        # without --confirm it prints the warning
cinegain overdrive off                 # values above +15 dB drop at once

# Filter on/off (off = pass-through; set, batch, preset and start keep this state):
cinegain filter                        # show
cinegain filter off
cinegain filter on

# Reset everything to factory settings (state is backed up first):
cinegain reset

# Stop the filter chain and release all audio streams:
cinegain stop

# Debug log (GUI, CLI and routing write to ~/.local/state/cinegain/cinegain.log):
cinegain log            # last 80 lines
cinegain log -n 300     # more lines
cinegain log --follow   # keep printing new lines
cinegain log --path     # where the file is
```

**Debug log.** Every stream that appears, moves or disappears (with program, sink and reason), every filter process start, stop or crash, every parameter push, the filters' meters every 10 s while audio flows, every GUI action and every error is written to the log (rotating, 5 × 2 MB). The stderr of each PipeWire filter process goes to `pipewire-<profile>.log` next to it. Set the level with `CINEGAIN_LOG_LEVEL` or `"log_level"` in `~/.config/cinegain/config.json` (default `DEBUG`), the directory with `CINEGAIN_LOG_DIR`. The tray menu has "Open debug log".

**Settings file.** `~/.config/cinegain/config.json` keeps the language, the view level (`view_level`), the sidebar width (`sidebar_width`) and the automatic hidden-volume reset (`volume_auto_reset`). Overdrive is never saved.

**Filter off.** "Filter ausschalten" switches every Cinegain filter to pass-through. Streams and sinks stay in place, so browsers do not pause the video. "Quit" or `cinegain stop` really stops the filter chain.

---

## DSP Architecture

```
Audio Input (L / R)
       │
       ▼
 [ Volume Stage (-30 dB .. +15 dB, overdrive +40 dB; rises at 10 dB/s, falls at once) ]
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
 [ Peak-Sensing Dialogue Leveler (fast peak-sensing cut, slow RMS boost) ]
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
