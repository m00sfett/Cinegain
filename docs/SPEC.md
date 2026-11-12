# Cinegain — Spezifikation

Stand: 0.5.2 (2026-11-12). Grundlage sind die Grill-Runden vom 2026-11-11. Diese Datei beschreibt, was Cinegain leisten soll und woran man das prüft. Wie es gebaut ist, steht im Code, in `DESIGN.md` (Gestaltung) und in `CHANGELOG.md` (Verlauf).

## 1. Zielbild

Cinegain ist ein Linux-Werkzeug für PipeWire mit zwei Hauptfunktionen:

1. **Lautstärke über das Systemmaß (Hauptfunktion).** Jedes Programm lässt sich lauter stellen, als die Systemlautstärke von 100 % erlaubt, sauber und ohne Übersteuern.
2. **Versteckte Lautstärken sichtbar machen, einstellen und zurücksetzen.** PipeWire führt pro Stream eine zweite Lautstärke, die kein Mixer zeigt. WirePlumber speichert sie pro Programm und stellt sie bei jedem neuen Stream wieder her. Cinegain ist das Analysewerkzeug dafür.

Die Filmton-Kette (Leveler, Kompressor, Sprachfokus) bleibt als Modus **Dynamisch · Beta** erhalten, ist aber nicht mehr das Hauptziel.

## 2. Nicht-Ziele

- Kein Ersatz für einen Systemmixer: Hardware- und Sink-Lautstärken bleiben unangetastet.
- Keine Verstärkung über die versteckte Lautstärke (sie geht höchstens auf 100 %). Lauter macht nur Cinegains Filter. Ob Werte über 100 % sinnvoll wären, steht in `notes/spaeter-analysieren.md` des Metaprojekts.
- Vorerst keine Eingänge und Mikrofone in der Analyse (ebenfalls dort notiert).
- Keine automatische Pegelanpassung im Modus Fest.

## 3. Hauptfunktion 1: Lautstärke

### 3.1 Skala

| Bereich | Lautstärke (`input_db`) | Make-up (Dynamisch) |
|---|---|---|
| Normal | −30 bis +15 dB | −6 bis +15 dB |
| Overdrive | −30 bis +40 dB | −6 bis +40 dB |

Das Plugin akzeptiert `input_db` von −30 bis +40 dB und `makeup_db` von −24 bis +40 dB. Gekappt wird in Python (`cinegain_overdrive`, `cinegain_modes.effective`), also auf jedem Weg: GUI, CLI, Tray, Router.

### 3.2 Overdrive

- Der Schalter sitzt in der Titelleiste, in jeder Ansichtsstufe.
- Einschalten geht nur über einen Warndialog mit Checkbox. „Einschalten“ ist erst nach dem Haken klickbar. In der CLI braucht es `cinegain overdrive on --confirm`.
- Solange Overdrive an ist, ist der Knopf orange gefüllt. Der Reglerbereich über +15 dB ist orange markiert, Werte darüber erscheinen orange.
- Overdrive gilt nur bis zum Neustart. Es liegt als Laufzeitdatei unter `$XDG_RUNTIME_DIR/cinegain/overdrive`. Wer die Router-Sperre übernimmt (GUI oder `cinegain watch`), schaltet es beim Start aus.
- Ausschalten oder ein Neustart kappt alle gespeicherten Werte über +15 dB sofort auf +15 dB, in Standard und in jedem Programm.

### 3.3 Sanftes Hochfahren

- Im Plugin steigen alle Verstärkungen höchstens mit 10 dB/s: Lautstärke, Bass, Stimmen, Höhen, Mitte, Seite, Make-up. Sie fallen sofort.
- Das gilt für jede Änderung: Regler, Preset, Speicherplatz, Tray, CLI, neu startender Filter, Wiedereinschalten nach „Filter ausschalten“.
- Ein frisch gestarteter Filter beginnt bei 0 dB (oder tiefer) und fährt hoch.
- Die Meterdatei meldet, wie viele dB noch fehlen (`ramp_db`). Der Hauptregler zeigt das als „fährt hoch … noch X dB“.
- Dasselbe gilt für Limiter-Decke, Leveler-Ziel und die Obergrenze des Levelers (`lev_max_boost_db`). Der Leveler selbst steigt im Normalbetrieb mit seiner eigenen Gleitzeit. Auf höchstens 10 dB/s begrenzt ist er nur beim Ausgleiten nach „Leveler aus“ (zurück auf 0 dB aus einer Absenkung) und nach „Filter an“, bis er sein Ziel zum ersten Mal erreicht hat. Absenkungen wirken immer sofort.
- Wird ein Stream auf eine andere Sink verschoben, die schon hochgefahren sein kann, sinkt seine versteckte Lautstärke vorher kurz auf −30 dB und blendet danach wieder auf den alten Wert ein. Dort gibt es also keine Lücke in der Rampe. Streams, die ohnehin leiser sind, bleiben unberührt (`VolumeGuard.duck`, Router).

### 3.4 Bedienung der Regler

- **Schrittweite:** normal 1 dB, mit Shift 0,1 dB, mit Strg 5 dB. Das gilt für Pfeiltasten, Mausrad, Klick in die Bahn und die ±-Knöpfe. Die Kompressor-Ratio macht 0,5 / 0,1 / 1,0.
- **±-Knöpfe:** rechts neben jedem Regler, mit Auto-Repeat beim Gedrückthalten. Sie nehmen keinen Tastaturfokus, die Pfeiltasten bleiben bei den Reglern.
- **Rechtsklick auf einen Regler:** setzt den neutralen Wert (0 dB, Ratio 1:1, Ceiling −1 dB). Das Hochfahren läuft dabei über die Rampe.
- **Schnellstufen (Fest):** 0, +3, +6, +9, +12, +15 dB. Gespeicherte Speicherplätze über +15 dB werden ohne Overdrive gekappt.

## 4. Hauptfunktion 2: Versteckte Lautstärken

### 4.1 Analyse-Ansicht

Sie ist ein eigener Tab, ab der Stufe Erweitert. Die Tabelle hat eine Zeile pro Knoten:

| Spalte | Inhalt |
|---|---|
| Programm / Knoten | Programmname, bei Stummschaltung mit Hinweis |
| Art | Programm, Cinegain-Ausgang, Cinegain-Eingang, Soundkarte |
| Versteckt | Node-Lautstärke in dB, in Profi zusätzlich linear |
| Mixer | lauteste Kanal-Lautstärke in % |
| Gespeichert | Wert in WirePlumbers `stream-properties` |
| Setzen | Regler 0–100 %, wirkt beim Loslassen |
| Zurücksetzen | aktiv, wenn abgesenkt |
| ID, node.name | nur in Profi |

- Programme und Cinegain-Knoten sind bearbeitbar, Soundkarten nur lesbar.
- Abgesenkte Zeilen sind dunkelrot hinterlegt.
- Bei offener Ansicht wird alle 0,5 s abgetastet, sonst prüft der Router alle 1,5 s.
- Die Zeitleiste zeigt die letzten 7 Tage aus dem Debug-Log (inklusive rotierter Dateien), höchstens 500 Einträge, filterbar nach Programm. Ereignisse: abgesenkt (alter → neuer Wert), automatisch zurückgeblendet, gesetzt (durch dich oder die CLI), Lautstärke voll zurückgesetzt.

### 4.2 Automatik und Reset

- **Automatisch zurücksetzen** (Standard: an, gespeichert in `config.json` als `volume_auto_reset`): Programme über Cinegain und Cinegains eigene Ausgänge werden auf 0 dB zurückgeblendet. Ist es aus, wird alles nur angezeigt und protokolliert.
- **Fades der versteckten Lautstärke:** höchstens 10 dB/s, höchstens 1 dB pro Schritt, mindestens 3 s Dauer (`FADE_S`, `MAX_RISE_DB_S`, `MAX_STEP_DB` in `cinegain_volume.py`). Der letzte Schritt landet genau auf dem Ziel. Das gilt für die Automatik, den Voll-Reset und das Zurückblenden nach dem Absenken beim Verschieben (siehe 3.3).
- **Lautstärke voll zurücksetzen** (Leiste, Analyse-Tab, Tray, `cinegain volume reset`):
  - versteckte Lautstärken auf 0 dB, normale Stream-Lautstärken (Mixer) auf 100 %. Nichts steigt schneller als 10 dB/s: Was Cinegain durch Absenken der eigenen Verstärkung ausgleichen kann, kommt sofort zurück, der Restpegel und der Mixer werden eingeblendet.
  - Stummschaltungen bleiben bestehen, weil Aufheben ein Sprung auf volle Lautstärke wäre. Die CLI meldet, wie viele Streams stumm geblieben sind.
  - WirePlumber-Datei bereinigen, vorher sichern
  - Cinegains eigene Verstärkung vorher um den zurückkommenden Betrag senken, höchstens bis −30 dB
- Im Bearbeitungskopf steht die versteckte Lautstärke des Ziels ab Erweitert immer, auch bei 0 dB.

### 4.3 CLI

- `cinegain volume [list] [--json]`
- `cinegain volume set <id|programm> <prozent>`
- `cinegain volume reset [programm]`

`cinegain volume reset --restart-wireplumber [--yes]` startet WirePlumber nach dem Reset neu, damit die bereinigten gespeicherten Werte nicht zurückgeschrieben werden. Ohne `--yes` fragt die CLI vorher nach, weil dabei alle Audio-Verbindungen neu aufgebaut werden und die Wiedergabe kurz pausieren kann.

`cinegain volume-reset` bleibt als alter Name erhalten.

**Filter:** `cinegain filter [on|off|status]` zeigt den Filter-Zustand oder schaltet ihn. Aus heißt Durchreichen ohne Verarbeitung. Der Zustand bleibt über `set`, `batch`, `preset` und `start` erhalten. Das Wiedereinschalten fährt die Verstärkung wie in 3.3 neu hoch.

## 5. Ansichtsstufen

| Stufe | Inhalt |
|---|---|
| Einfach | Programmliste, Hauptregler mit ±, Schnellstufen, Filter an/aus, Overdrive |
| Erweitert | dazu alle Fest-Regler, Film-Presets, Speicherplätze, Pegelmeter, Modus-Umschalter, versteckte Lautstärke im Kopf, Analyse-Tab |
| Profi | dazu Rohwerte (Profil-ID und PID im Kopf, Node-ID, node.name, Linearwerte), Log-Tab |

- Neue Installationen und bestehende ohne gespeicherte Stufe starten in **Einfach**. Danach merkt sich die App die zuletzt gewählte Stufe (`view_level`).
- Die Stufe blendet nur aus. Ausgeblendete Werte wirken weiter. Läuft das Ziel in Einfach dynamisch, sagt der Kopf das.

## 6. Oberfläche, übergreifend

- **Beta-Kennzeichnung:** gestrichelter Rahmen statt β, immer mit Tooltip „Beta-Funktion“. Das gilt für die Film-Presets, „Dynamisch · Beta“, dynamische Speicherplätze und das Etikett „Dynamisch“ in der Programmliste.
- **Programmliste:** ziehbarer Trenner, Startbreite 280 px, mindestens 200 px, höchstens die halbe Fensterbreite. Die Breite wird gespeichert (`sidebar_width`), ein Doppelklick auf den Trenner setzt sie zurück.
- **Logo:** Das R²W²-Logo in der Titelleiste verlinkt auf https://github.com/m00sfett/Cinegain.

## 7. Akzeptanzkriterien

Die Kürzel verweisen auf die Offline-Tests (`tests/`).

| Nr. | Kriterium | Nachweis |
|---|---|---|
| A1 | Lautstärke-Werte über +15 dB werden ohne Overdrive auf +15 dB gekappt, unter −30 dB auf −30 dB | `test_modes_volume.TestOverdriveScale` |
| A2 | Mit Overdrive sind bis +40 dB möglich, danach wird bei +40 dB gekappt | ebd. |
| A3 | Overdrive aus kappt gespeicherte Werte von Standard und Programmen sofort; Werte innerhalb der Skala bleiben unverändert | `test_overdrive_off_caps_saved_values`, `test_overdrive_widens_the_scale_and_off_caps_at_once` |
| A4 | Overdrive geht nur nach bestätigtem Dialog an | `test_overdrive_needs_the_confirmed_dialog`, CLI ohne `--confirm` endet mit Exit 2 |
| A5 | Ein frischer Filter mit +20 dB liegt nach 0,5 s bei etwa +5 dB und nach 2,6 s bei +20 dB | `test_dsp_stages.test_boost_rises_slowly_on_a_fresh_instance` |
| A6 | Herunterregeln wirkt direkt nach den 5 ms Vorlauf, erneutes Hochregeln fährt wieder an | `test_turning_down_is_immediate_and_up_again_ramps` |
| A7 | Nach „Filter ausschalten“ und wieder an fährt die Verstärkung neu hoch | `test_bypass_fades_the_boost_in_again` |
| A8 | Die Meterdatei meldet die verbleibende Rampe | `test_meter_reports_the_remaining_ramp` |
| A9 | Schritte 1 / 0,1 / 5 dB für Tasten und ±-Knöpfe; Kappung am Skalenende | `test_steps_keys_wheel_and_buttons` |
| A10 | Rechtsklick setzt neutral (0 dB, Ratio 1:1, Ceiling −1 dB) | `test_right_click_resets_to_neutral` |
| A11 | Einfach zeigt nur den Hauptregler, Overdrive bleibt sichtbar; Stufe wird gespeichert, andere Einstellungen bleiben erhalten | `test_view_levels_show_and_hide_and_are_remembered` |
| A12 | Analyse listet Knoten, Hardware nur lesend, Reset setzt 100 % | `test_analysis_panel_lists_sets_and_resets`, `TestAnalysis` |
| A13 | Zeitleiste liest Ereignisse der letzten 7 Tage aus dem Log, ältere nicht | `test_history_parses_log_events` |
| A14 | Ohne Automatik blendet der Wächter nichts zurück, sondern meldet nur | `test_guard_without_auto_only_reports`, `test_auto_reset_switch_reaches_the_guard` |
| A15 | Beta ist gestrichelt markiert, ohne β im Text | `test_beta_marks_are_dashed_not_symbols` |
| A16 | Sidebar-Breite ist verstellbar, wird gespeichert und lässt sich zurücksetzen | `test_sidebar_width_is_adjustable_and_remembered` |
| A17 | Der Kopf zeigt die versteckte Lautstärke auch bei 0 dB | `test_header_shows_hidden_volume_even_at_0_db` |
| A18 | Schnellstufen +3 bis +15 dB; kein Werk-Preset über +15 dB | `test_boost_presets_cover_the_normal_scale` |

**Am echten Desktop zu prüfen** (nicht automatisiert):
- hörbares Hochfahren nach einem Preset-Sprung
- Overdrive-Dialog und Kappung beim Ausschalten
- Analyse-Tab mit echten PipeWire-Knoten
- Trenner ziehen
