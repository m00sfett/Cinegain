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
