# Cinegain — Gestaltung (CI-Designsystem Rot-Gelb-Schwarz)

> Verbindliche visuelle Richtung, abgeleitet aus `/vault/kontext/ci-designsystem.md` und `boksy2/DESIGN.md` (Red Star #1, Nachtnutzungs-Variante).

## Leitlinien

1. **Plakativ, technisch nüchtern, sofort ablesbar:** Rote Bannerfläche mit gelber Condensed-Typografie, warme dunkle Panels mit dunkelroten Rändern, keine generische Blau/Grau-Dashboard-Optik.
2. **Rot ist Marke und Ausschalt-/Alarmzustand, Gelb ist Aktion:** Das Titelbanner trägt `#B11218` mit `#FFD21F`. Alle interaktiven Bedienelemente (aktive Presets, Slider-Füllungen, Griffe, Tastatur-Fokusring, ausgewählte Audio-Quellen) laufen über Signal-Gelb `#FFD21F` / `#E0B83A`.
3. **Klare Hierarchie ohne sinnlose Nummerierung:** Keine Listennummern (`1.`–`5.`) vor Reglern. Die **Direkt-Verstärkung (Haupt-Lautstärke)** sitzt als hervorgehobenes Hero-Steuerelement (`#1F1712`, stärkere Spur, große `Fira Mono`-Anzeige) über den übrigen Reglern.
4. **Vollständige Tastatur-Steuerung vom Sofa aus:** `↑`/`↓` schaltet nahtlos durch alle 12 Regler (inkl. Spaltenwechsel), `←`/`→` ändert den Wert in `1,0 dB`-Schritten (`Shift + ←/→` für `0,1 dB`). Die fokussierte Regler-Zeile erhält einen unübersehbaren gelben Rahmen (`2px solid #FFD21F`), aufgehellte Fläche (`#271C16`) und Fokus-Pfeil `▶`.
5. **Preset-Wahrheit durch Wertabgleich:** Jedes Werk-Preset (`0 dB`, `+5`..`+25 dB`, Kino-Profile) und jedes der 8 eigenen Presets (`Eigenes 1`..`8`) wird automatisch gelb hervorgehoben, sobald alle 12 Reglerwerte mit ihm übereinstimmen.

## Farbpalette

| Rolle | Hex | Verwendung |
|---|---|---|
| `bgMain` | `#080604` | Fenster-Grundfläche |
| `bgPanel` | `#14100D` | Standard-Karten (Presets, Spalten, Quellen) |
| `bgPanelStrong` | `#1F1712` | Hero-Block „Direkt-Verstärkung", aktive Zeilen |
| `bgPanelFocus` | `#271C16` | Tastatur-fokussierte Regler-Zeile |
| `redBanner` | `#B11218` | Kopfleiste / Titelbanner |
| `redPrimary` | `#D01822` | Ausschalt-Button / Primärrot |
| `redHover` | `#FF5546` | Hover auf roten Aktionen |
| `borderRed` | `#6E1516` | Panel-Ränder und Trenner |
| `yellowTitle` | `#FFD21F` | Banner-Titel, aktive Presets, Slider-Füllung, Fokusrahmen, dB-Werte |
| `yellowText` | `#E0B83A` | Sekundäre gelbe Akzente, Gruppen-Titel |
| `yellowContainer` | `#3D2C07` | Aktiver Preset-/Fokus-Container |
| `ink` | `#EDE6DC` | Ruhiger Lesetext für Regler-Labels bei Nachtnutzung |
| `inkMuted` | `#E2C89B` | Sekundärtext, Beschreibungen |
| `stateRunning` | `#6FBF8B` | Status „AKTIV" / „Läuft durch Cinegain" |
| `stateWaiting` | `#E8C462` | Status „Pausiert (5-Min.-Gedächtnis)" |
| `stateIdle` | `#A2968A` | Status „INAKTIV" / „Direkt auf Lautsprecher" |

## Typografie

- **Titel & Sektionsköpfe:** `Fira Sans Condensed` (Bold / SemiBold)
- **Regler-Labels & Buttons:** `Fira Sans` (Medium / SemiBold)
- **Pegel- & Messwerte (`dB`, `:1`):** `Fira Mono` (Bold)
