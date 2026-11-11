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

## [0.5.1] - Unreleased

Patch-Version: nur Packaging und Release-Ablauf, das Programm selbst ist unverändert.

### Changed
- **Release-Notes aus dem Changelog:** Der Release-Workflow übernimmt den Abschnitt der getaggten Version aus `CHANGELOG.md` als Text des GitHub-Release. Bisher wurden die Notes aus PR-Titeln erzeugt. Fehlt der Abschnitt, bricht der Workflow vor dem Build ab.

### Added
- **Prüfsummen:** Jedes Release enthält eine Datei `SHA256SUMS` für Tarball, `.deb` und `.rpm`. Prüfen mit `sha256sum -c SHA256SUMS --ignore-missing`.
- **Versionsabgleich:** `tests/test_version_ledger.py` prüft, dass CLI, GUI, Fußzeilen, `install.sh`, PKGBUILD, RPM-Spec samt `%changelog`, `build-deb.sh`, README und Changelog dieselbe Version tragen und die PipeWire-Mindestversion 0.3.50 unverändert bleibt. Der Release-Workflow führt ihn mit dem Tag aus und stoppt bei Abweichung.

## [0.5.0] - 2026-11-11

Minor-Version mit Freigabe des Nutzers (Grill-Runde 0.5.0, Q14b). Die Produktausrichtung ändert sich: Hauptfunktion ist die Lautstärke über das Systemmaß, zweite Hauptfunktion die Analyse versteckter Lautstärken. Dazu kommen Overdrive, Ansichtsstufen und eine neue Skala. Spezifikation: `docs/SPEC.md`.

### Added
- **Overdrive:** erweitert Lautstärke und Make-up von +15 dB auf +40 dB.
  - Einschalten nur über einen Warndialog mit Checkbox, in der CLI mit `cinegain overdrive on --confirm`.
  - Gilt nur bis zum Neustart. Ausschalten oder Neustart kappt gespeicherte Werte über +15 dB sofort.
  - Der Schalter steht in jeder Ansichtsstufe in der Titelleiste. Die Zone über +15 dB ist orange markiert.
- **Sanftes Hochfahren im Plugin:** Alle Verstärkungen steigen höchstens mit 10 dB/s und fallen sofort.
  - Das gilt für Regler, Presets, Tray, CLI, neu startende Filter und das Wiedereinschalten nach „Filter ausschalten“.
  - Der Hauptregler zeigt, wie viele dB noch fehlen. Die Meterdatei hat dafür das neue Feld `ramp_db`.
- **±-Knöpfe neben jedem Regler** mit Auto-Repeat. Schritte 1 dB, mit Shift 0,1 dB, mit Strg 5 dB. Dieselben Schritte gelten jetzt auch für Pfeiltasten, Mausrad und Klick in die Bahn.
- **Rechtsklick auf einen Regler** setzt ihn auf den neutralen Wert.
- **Analyse-Ansicht (Tab „Analyse“):**
  - Liste aller Ausgabe-Streams, Cinegain-Knoten und, nur lesend, der Soundkarten.
  - Pro Knoten: versteckte Lautstärke, Mixer-Lautstärke, Stummschaltung, gespeicherter WirePlumber-Wert, Regler 0–100 %, Zurücksetzen.
  - Zeitleiste der letzten 7 Tage aus dem Debug-Log.
  - Die automatische Rückblende lässt sich abschalten. Bei offener Ansicht wird alle 0,5 s abgetastet.
- **Ansichtsstufen Einfach / Erweitert / Profi.** Die Stufe wird gespeichert. Profi zeigt zusätzlich Rohwerte und das Debug-Log in einem Tab.
- **Versteckte Lautstärke im Bearbeitungskopf** ab Erweitert immer sichtbar, auch bei 0 dB.
- **Verstellbare Programmliste:** ziehbarer Trenner, Startbreite 280 px, Breite gespeichert, Doppelklick setzt zurück.
- **CLI:**
  - `cinegain overdrive [on --confirm|off|status]`
  - `cinegain volume [list|set|reset]`. `volume-reset` bleibt als alter Name.
  - `cinegain status` nennt den Overdrive-Zustand.
- Das R²W²-Logo in der Titelleiste verlinkt auf das GitHub-Projekt.
- `docs/SPEC.md` mit Zielbild, Nicht-Zielen und Akzeptanzkriterien.

### Changed
- **Skala:**
  - Lautstärke von −30 bis +15 dB, vorher −12 bis +25 dB.
  - Make-up höchstens +15 dB, mit Overdrive +40 dB.
  - Die Plugin-Grenzen sind erweitert: `input_db` −30 bis +40 dB, `makeup_db` bis +40 dB.
  - Gespeicherte Werte über +15 dB werden beim Start gekappt.
- **Schnellstufen:**
  - Die Lautstärke-Presets heißen jetzt +3, +6, +9, +12 und +15 dB (vorher +5 bis +25 dB). Neue IDs `boost_3db` bis `boost_15db`.
  - „Max-Kino“ startet bei +15 dB statt +18 dB.
  - Das Tray-Menü bietet +6 und +12 dB.
- **Beta-Kennzeichnung:** gestrichelter Rahmen und Tooltip statt β. Das gilt für die Film-Presets, „Dynamisch · Beta“, dynamische Speicherplätze und das Etikett „Dynamisch“ in der Programmliste.
- **Voll-Reset:** gleicht Cinegains Verstärkung bis −30 dB aus (vorher −12 dB).
- **Einstellungen:** `config.json` wird zusammengeführt statt überschrieben. Sprache, Ansichtsstufe, Sidebar-Breite und Automatik bleiben nebeneinander erhalten.
- **Doku:** README, PRODUCT.md und DESIGN.md sind auf die neue Ausrichtung umgestellt. Untertitel und Tooltip nennen die neue Skala. Der Reset-Dialog spricht von 6 statt 8 Speicherplätzen.

### Fixed
- **Paketmetadaten (#21):** Die PipeWire-Mindestversion in .deb, .rpm und README steht wieder auf 0.3.50. Frühere Versionssprünge hatten sie über 0.3.60 auf 0.4.00 verbogen.

---

## [0.4.0] - 2026-11-11

Minor-Version mit Freigabe des Nutzers: neuer Betriebsmodus pro Programm und umgebaute Preset-Leiste.

### Added
- **Modus pro Programm und für Standard:** „Fest“ und „Dynamisch · Beta“.
  - **Fest** ist die neue Hauptfunktion: feste Lautstärke-Anhebung bis +25 dB plus feste Klangfarbe (Stimmen, Bass, Höhen, Stereo-Breite). Keine Signalanalyse, Leveler und Kompressor bleiben neutral. Der Look-Ahead-Limiter schützt immer vor Übersteuern, die Grenze liegt bei −1 dBFS.
  - **Dynamisch · Beta** ist die bisherige Filmton-Kette.
  - Jeder Modus hat einen eigenen Wertesatz, Umschalten verliert nichts.
  - Der Wechsel überträgt nur Parameter: kein Neustart der Sink, kein Knacken, keine Browser-Pause.
  - Umschalter im Bearbeitungskopf. Ein β markiert dynamische Programme in der Liste, Film-Presets und Speicherplätze.
  - Schaltet man bei einem Programm um, das Standard folgt, bekommt es eine eigene Einstellung mit den Werten von Standard.
- **Wächter für versteckte Lautstärke:** PipeWire führt pro Stream eine zweite Lautstärke, die kein Mixer zeigt. WirePlumber merkt sie sich und setzt sie bei jedem neuen Stream wieder. Am 2026-11-11 gemessen: Chrome −48 dB, Electron-Apps −84 dB, Cinegains Chrome-Ausgang −24 dB.
  - Für Programme, die durch Cinegain laufen, und für Cinegains eigene Ausgänge blendet Cinegain die Lautstärke über 3 s zurück auf 0 dB, ohne Sprung und ohne Reglerwerte zu ändern.
  - Für alle anderen Programme zeigt Cinegain ein Badge an der Programmzeile, einen Hinweis im Kopf und eine Leiste unter dem Banner.
  - Jede Absenkung wird mit Wert und Uhrzeit protokolliert.
- **„Lautstärke voll zurücksetzen“** (Leiste, Tray, `cinegain volume-reset`):
  - Versteckte Lautstärken gehen auf 0 dB, normale Stream-Lautstärken auf 100 %, Stummschaltung aus.
  - Die gespeicherten WirePlumber-Werte werden bereinigt, vorher gibt es eine Sicherung.
  - Programme mit eigener Einstellung und Standards Ausgang bekommen vorher ihre Verstärkung um den zurückkommenden Betrag gesenkt (höchstens bis −12 dB). Programme, die Standard folgen, werden eingeblendet.
  - Hardware- und Sink-Lautstärken bleiben unangetastet.
  - „Zurücksetzen“ im Kopf wirkt nur auf das gewählte Programm.
- CLI: `cinegain mode [fixed|dynamic] [--app …]`, `cinegain batch … --mode …`, `cinegain volume-reset [--app …] [--json]`. `cinegain status --json` liefert `mode` und `sets`. `cinegain watch` führt den Wächter mit.

### Changed
- „Weitere Einstellungen“ entfällt: Lautstärke-Stufen, Film-Presets und Speicherplätze sind immer sichtbar.
- 6 statt 8 eigene Speicherplätze. Sie merken sich den Modus mit. Alte Plätze bekommen ihren Modus aus den Werten, Platz 7 und 8 entfallen.
- Lange Reglertitel umbrechen in schmalen Fenstern, damit beide Spalten nebeneinander passen.
- Reglerreihenfolge: links Direkt-Verstärkung, Stimmen, dann Leveler und Mitte. Rechts Bass, Höhen, Stereo-Breite, dann die Dynamik-Regler.
- **Migration:**
  - Standard startet nach dem Update im Modus Fest und neutral (0 dB).
  - Bisherige eigene Einstellungen bleiben als Dynamik-Wertesatz erhalten. Programme mit Leveler oder Kompressor laufen weiter dynamisch und klingen unverändert. Alle anderen starten im Modus Fest neutral.
  - `apps.json` ist jetzt Version 3 und hat pro Programm `mode` und `sets`. `state.json` hat ebenfalls `mode` und `sets`.

## [0.3.6] - 2026-11-11

### Fixed
- **Zwei Cinegain-Instanzen schoben Streams hin und her (#18):** Lief eine zweite GUI (oder `cinegain watch` neben der GUI), verteilten beide die Streams. Bei der Installation von 0.3.5 wechselte Chrome so 15 s lang alle 1,5 s zwischen eigener Einstellung und Standard.
  - Jetzt verteilt nur ein Prozess die Streams. Dafür gibt es eine Sperre (`flock` auf `$XDG_RUNTIME_DIR/cinegain/router.lock`), die nach einem Absturz von selbst verschwindet.
  - Ein zweiter GUI-Start holt das laufende Fenster nach vorn und beendet sich.
  - `cinegain watch` startet nicht, solange die GUI läuft. Umgekehrt meldet die GUI, wer die Streams bereits verteilt.

## [0.3.5] - 2026-11-11

### Added
- **Debug-Log** für die Fehlersuche: GUI, CLI und Routing schreiben in eine Datei (`~/.local/state/cinegain/cinegain.log`, 5 × 2 MB rotierend, Pfad über `CINEGAIN_LOG_DIR` änderbar). Protokolliert werden:
  - neue, verschobene und verschwundene Streams mit Programm, Sink und Grund
  - Start, Stopp und Absturz der Filterprozesse
  - übertragene Reglerwerte
  - alle 10 s die Pegel jedes Filters, solange Ton läuft
  - Bedienschritte (Sprache, Preset, Programmwahl, Ein/Aus, Reset, Beenden)
  - fehlgeschlagene `pactl`/`pw-cli`-Aufrufe, Qt-Warnungen und unbehandelte Exceptions
- Die Fehlerausgabe jedes PipeWire-Filterprozesses landet in `pipewire-<profil>.log` daneben.
- Geschrieben werden nur Änderungen, im Normalbetrieb also kaum etwas. Stufe über `CINEGAIN_LOG_LEVEL` oder `"log_level"` in `config.json` (Standard `DEBUG`).
- `cinegain log` zeigt das Log (`-n 200`, `--follow`, `--path`). Im Tray-Menü gibt es „Debug-Log öffnen“.
- Die Pegelanzeige nennt, welchen Filter sie gerade misst („Gemessen: Google Chrome“).

### Fixed
- **Leiser Ton nach einem Browser-Update:** Aktualisiert der Paketmanager Chrome (oder eine Electron-App wie ChatGPT) während er läuft, meldet Linux das Programm als `chrome (deleted)`. Cinegain hielt das für ein neues Programm ohne eigene Einstellung. Chrome lief dann plötzlich über den neutralen Standard, also ohne Verstärkung. Der Zusatz wird jetzt ignoriert. Bereits entstandene Doppel-Einträge werden beim Start zusammengeführt, die zuletzt benutzte Einstellung gewinnt (#16).
- **„Filter ausschalten“ pausierte Videos im Browser:** Ausschalten entfernte alle Cinegain-Sinks, und Browser pausieren, wenn ihr Ausgabegerät verschwindet. Aus/Ein schaltet jetzt alle Filter auf Durchreichen (Bypass). Streams und Sinks bleiben, wo sie sind. Neue Filter starten im Aus-Zustand ebenfalls durchgereicht. Ganz gestoppt wird nur noch über Beenden oder `cinegain stop` (#14).
- **Sprachwechsel:** Tooltip der Pegelanzeige und Preset-Namen im Tray-Menü blieben in der Startsprache. Die Reglertitel nach Fokuswechsel hatte schon 0.3.4 behoben (#15).
- **Pegelanzeige maß das falsche Ziel:** Mit „Standard“ ausgewählt zeigte sie den leerlaufenden Standard-Filter (Leveler 0 dB), während Chrome über seine eigene Einstellung lief. Sie zeigt jetzt den ersten Filter, durch den tatsächlich Ton fließt: zuerst das ausgewählte Ziel, dann die gerade spielenden Programme (#16).

## [0.3.4] - 2026-11-10

- Schlägt die PipeWire-Abfrage fehl, meldet die CLI einen unbekannten Status (Exitcode 2). Die GUI wartet auf die nächste Abfrage, bevor sie Start oder Stopp anbietet. Bild↑ und Bild↓ wechseln jetzt auch aus einer fokussierten Programmzeile das Programm und nehmen den Tastaturfokus mit.

- Die GUI zeigt zuerst Filmprofile und Hauptregler. Weitere Einstellungen öffnen Feinregler, Boost-Stufen und eigene Vorlagen. Kleine Fenster unterstützen Scrollen.
- Tab wechselt zwischen Bedienelementen, F6 zwischen Reglerspalten. Geladene Vorlagen bleiben bis zum ausdrücklichen Speichern erhalten. Bei fehlender Statusantwort zeigt die GUI einen unbekannten Zustand und prüft erneut.

## [0.3.3] - 2026-11-10

### Added
- **Live meter strip** under the presets: input peak, leveler gain, compressor and limiter reduction of the filter that shapes the selected program (README promised real-time feedback). PipeWire does not expose control output ports, so the plugin writes the four values to a small memory-mapped file (`$XDG_RUNTIME_DIR/cinegain/meter-<pid>.bin`) and the GUI polls it. Bars go empty when no audio flows.
- **Auto-saving user slots:** a slot you loaded (or saved to) follows your slider changes until you pick a factory preset, another slot or another program. This reverses the 0.3.0 "slots are templates" behaviour to match the README.
- **.rpm package:** the release workflow now builds `cinegain-<version>-1.x86_64.rpm` from `packaging/rpm/cinegain.spec` (installs the plugin to `/usr/lib/ladspa`, where Cinegain looks for it).
- Tests: `tests/test_dsp_stages.py` (high-pass off, leveler cut speed, meter file) and GUI tests for auto-save and meters; 61 tests.

### Changed
- **Leveler** is now peak-sensing as documented: boosts still follow the slow RMS, but cuts react about 8x faster and to loud peaks directly. After a quiet-to-loud jump the output settles in about 2 s instead of over 10 s.
- Switching the leveler on with the boost slider also gives it a 6 dB cut range (it could only boost before).
- **High-pass at its minimum (10 Hz) is off** and Neutral/boost presets use it, so *Neutral (0 dB)* is now bit-transparent (it used to cut -3 dB at 20 Hz).

### Fixed
- Per-program editing: moving *Leveler: max boost* now switches the program's leveler on (and off again at 0), exactly like it already did for Standard. Before, the slider stored a value but the leveler stayed off, so nothing changed in the sound.
- `cinegain route` no longer crashes with `AttributeError: 'AppView' object has no attribute 'profile'`; it lists each playing program with `off`, `standard` or `own` and its sink.

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
