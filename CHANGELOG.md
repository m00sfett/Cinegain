# Changelog

All notable changes to **Cinegain** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## How to read this changelog & Versioning Rules (KeepaDB convention)

- **MAJOR (`X.0.0`):** Incompatible changes, such as LADSPA ABI modifications, PipeWire filter-chain configuration restructuring, or breaking CLI/IPC protocol updates.
- **MINOR (`1.X.0`):** Backward-compatible new features, including additional DSP filters, UI layout expansions, new factory presets, or additional localized languages.
- **PATCH (`1.0.X`):** Backward-compatible bug fixes, performance and memory optimizations, UI theme refinements, or packaging updates.

Categories follow the standard Keep a Changelog taxonomy: `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, and `Security`.

---

## [0.3.2] - 2026-11-10

### Fixed
- **Crash on the roteson.de logo:** clicking the logo in the title banner aborted the whole app, because the click handler returned a value to Qt. The link now opens through a proper handler and falls back to `xdg-open` if the desktop cannot open it directly. A failure to open the browser can no longer take the app down.

---

## [0.3.1] - 2026-11-10

### Changed
- **Standard starts neutral:** Fresh installs and the factory reset start Standard with the values of the "Neutral (0 dB)" preset (0 dB, flat EQ, leveler and compressor off) instead of the dialogue-boost values. Programs that follow Standard sound untouched until you change it. Existing saved Standard values are not changed by the update.
- **Logos in the title banner:** the Cinegain logo left of the title, the roteson.de emblem (opens roteson.de) and the R2W2 Development emblem right of the quit button.

---

## [0.3.0] - 2026-11-10

### Added
- **Program sidebar:** Programs now sit in a sidebar on the left, with *Standard* pinned on top. Select a program and the presets and sliders on the right edit exactly that program. A header above the presets names the target and its state.
- **Own setting per program:** The first slider move or preset you load while a program is selected gives it its own complete setting, copied from Standard. *Back to Standard* discards it. Programs with their own setting run through their own filter sink, started on demand and stopped after 45 s without use.
- **On/off switch per program:** Each row has a switch that routes the program through Cinegain or straight to the speakers. Its setting is kept while it is off.
- **Keyboard:** `Page Up` / `Page Down` switch the selected program.
- **Folding:** Programs silent for more than seven days fold away under *Silent for a while*.
- **CLI:** `cinegain apps on|off|load|standard` and `cinegain templates` (alias `profiles`).
- **Tests:** `tests/test_gui_programs.py` drives the real window offscreen (selection, editing, templates, PageDown, back to Standard, switch). `tests/test_routing.py` now covers the new model and the migration (34 tests).

### Changed
- **Presets and user slots are templates:** A click loads their values into the selected program or into Standard. User slots no longer save every slider move in the background; right-click a slot to store the current values in it.
- **Tray presets** always apply to Standard, since the tray has no selection.
- **The window opens on the program that is playing**, if exactly one is. Otherwise Standard is selected. The selection never jumps by itself afterwards.
- **`apps.json` format version 2:** per program an on/off switch and either its own parameters or none. Version 1 files from 0.2.0 are migrated: programs are kept, their profile choices are dropped (media programs start switched on and following Standard), and the old file is kept as `apps.v1.json`. The factory reset also removes that backup.
- **Layout:** The program block at the bottom is gone; the window is wider and lower (about 1400×880).

### Removed
- The per-row profile drop-down from 0.2.0 and the CLI commands `apps set` and `profile-sync`.

### Fixed
- Long program names and status texts no longer push the switch out of the sidebar row; they are shortened with "…" and shown in full as a tooltip.

---

## [0.2.0] - 2026-11-10

### Added
- **Profiles per program:** Every program gets its own profile: *Off* (direct to the speakers), *Standard* (the main sliders) or any factory preset or filled user slot. Programs on a preset or user slot run through their own filter sink (`cinegain_<profile>`), started on demand and stopped after 45 s without use. Two programs can therefore play at the same time with different processing. Profile sinks never become the default output.
- **Permanent program list:** Programs no longer vanish after five minutes of silence. Every program that ever played audio is kept in `~/.config/cinegain/apps.json` with its profile, across pauses, restarts and reboots. Right-click a program to rename it, restore its volume or forget it.
- **Reset everything:** A button in the window footer and `cinegain reset` return sliders, user presets, program list and language to factory settings. Streams are moved off Cinegain, the volume of known programs is set back to 100 % and sticky Cinegain targets are removed from WirePlumber. The previous state is first backed up to `~/.local/share/cinegain/backups/<timestamp>/`.
- **Low-volume badge:** A program whose own stream volume is turned down (or muted) shows a badge; one click sets it back to 100 %.
- **CLI:** New commands `apps` (list, set, rename, forget), `profiles`, `profile-sync`, `watch` (routing without the GUI) and `reset`. `status --json` also reports running profile sinks and the version.
- **Tests:** `tests/test_routing.py` covers the registry, profile routing, idle shutdown, config generation, pactl parsing and the reset (25 tests, no PipeWire needed).

### Changed
- **Programs are identified by name and binary:** Electron apps that report `application.name = "Chromium"`, such as the ChatGPT desktop client, get their own entry and label instead of being merged with the browser. They are no longer routed through Cinegain by default just because they claim to be Chromium.
- **Routing only moves what is on the wrong sink:** Previously every stream was moved on every heartbeat. A program set to *Off* is only pulled off Cinegain; a manual choice of another hardware output (e.g. HDMI in pavucontrol) is left alone.
- **Shared core:** CLI and GUI now use the same modules (`ui/cinegain_*.py`); the in-memory `stream_manager.py` was removed. Runtime files moved from `/tmp` to `$XDG_RUNTIME_DIR/cinegain/`; the old `/tmp/cinegain.pid` is still cleaned up on stop. Settings are written atomically.
- **Program rows update in place:** The list is no longer rebuilt every 1.5 s, so an open profile drop-down stays open.
- **Window:** Slightly taller so the program list shows more rows. The footer version comes from the app version instead of a hard-coded string.
- **systemd user service:** `config/cinegain.service` pointed at a stale path. It now runs `cinegain start` and `cinegain watch`.

### Fixed
- **`&` in titles and preset names:** Qt turned it into a keyboard mnemonic, so "Film & Serie" showed as "Film _Serie". It is displayed correctly now.
- **`cinegain stop` could kill unrelated processes:** It stopped every process whose command line contained "pipewire -c" and "cinegain", including shells that merely mentioned those words. Only PipeWire processes started with a Cinegain config are stopped now.

---

## [0.1.1] - 2026-11-10

### Fixed
- **Indonesian UI was never selected:** The translation was keyed as `in`, the obsolete ISO 639-1 code, so Indonesian system locales (`id_ID`) fell back to English. The key is now `id`. The legacy code `in` is still accepted for system locales and saved configs.

### Changed
- **Limiter description corrected:** The limiter works on sample peaks with a 5 ms look-ahead, not on true peaks. Measured overshoot above the ceiling is about 0.2 dB, and the output never exceeds 0 dBFS. README, package descriptions and the German ceiling tooltip say so now. The DSP behaviour itself is unchanged.
- **Regression tests:** `tests/test_limiter_ceiling.py` checks the limiter ceiling against a fresh build of `cinegain.c`. `tests/test_i18n.py` checks the language mapping and the translation key set.

---

## [0.1.0] - 2026-11-09

### Added
- **Realtime LADSPA Audio DSP Engine (`cinegain.so`):**
  - C99 low-latency LADSPA plugin (Unique ID `7821`, label `cinegain_dsp`).
  - Fast 2-stage biquad shelving filters for low-end sub-bass control (120 Hz) and high-frequency brilliance (8 kHz).
  - Dialogue presence bandpass peaking filter (2.5 kHz) for enhanced speech intelligibility.
  - Mid/Side matrix processing with center dialogue isolation and stereo ambience / sound-effect attenuation.
  - Smooth RMS dialogue leveler with peak look-ahead to automatically bring up quiet whispers.
  - Feedforward soft-knee compressor with adjustable threshold, ratio, and makeup gain.
  - Zero-overshoot true-peak look-ahead brickwall limiter protecting against digital clipping.
- **Pure Volume Boost:**
  - Dedicated direct gain stage providing immediate hardware/software amplification from -12 dB up to +25 dB.
  - Quick-preset buttons for clean pure boosts: `+5 dB`, `+10 dB`, `+15 dB`, `+20 dB`, and `+25 dB`.
- **Modern Desktop GUI (`cinegain-gui`):**
  - Designed in Red-Yellow-Black CI (`#B11218`, `#FFD21F`, `#080604`) matching the project palette.
  - Two-column side-by-side layout: Hero Direct Boost and Main Speech Focus on the left; Fine-Tuning (EQ, Compressor, Limiter) on the right.
  - Full keyboard navigation: `↑` and `↓` arrow keys to cycle through all controls; `←` and `→` for 1.0 dB steps; `Shift + ←`/`→` for 0.1 dB fine adjustment.
  - Active focused control highlighted with golden border and glow indicator.
- **Presets & Custom User Slots:**
  - Factory presets: Neutral (0 dB), Pure Boosts (+5 dB to +25 dB), Film & Serie (Dialogue Boost), Night Mode, and Max-Kino.
  - 8 auto-saving user preset slots stored in `~/.config/cinegain/user_presets.json`.
  - Visual glowing feedback for active presets matching current slider values.
  - Context menu to rename or clear custom preset slots.
- **Per-Application Stream Routing & Memory:**
  - Interactive audio source checklist tracking active playback applications via PipeWire PulseAudio bridge.
  - 5-minute memory preventing UI flickering or accidental unrouting when media is temporarily paused.
- **Internationalization (i18n):**
  - Comprehensive localization support for 19 languages (matching KeepaDB): Arabic, German, English, Spanish, French, Hindi, Indonesian, Italian, Japanese, Korean, Dutch, Polish, Portuguese, Russian, Turkish, Ukrainian, Vietnamese, Simplified Chinese, Traditional Chinese.
  - Title bar language selector button with Material Translate vector icon (`A / 文`) allowing instant live UI language switching without restart.
  - Preference persistence in `~/.config/cinegain/config.json`.
- **Packaging & CLI Integration:**
  - Unified CLI command (`cinegain`) for headless daemon management (`start`, `stop`, `restart`, `status`, `set`, `get`, `preset`).
  - System tray icon with quick preset switching, minimize-to-tray, and graceful shutdown.
  - Complete AGPLv3 licensing and documentation.
