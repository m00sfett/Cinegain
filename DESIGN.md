# Cinegain — Gestaltung (CI-Designsystem Rot-Gelb-Schwarz)

> Verbindliche visuelle Richtung, abgeleitet aus `/vault/kontext/ci-designsystem.md` und `boksy2/DESIGN.md` (Red Star #1, Nachtnutzungs-Variante).

## Leitlinien

1. **Plakativ, technisch nüchtern, sofort ablesbar:** Rote Bannerfläche mit gelber Condensed-Typografie, warme dunkle Panels mit dunkelroten Rändern, keine generische Blau/Grau-Dashboard-Optik.
2. **Rot ist Marke und Ausschalt-/Alarmzustand, Gelb ist Aktion:** Das Titelbanner trägt `#B11218` mit `#FFD21F`. Alle interaktiven Bedienelemente (aktive Presets, Slider-Füllungen, Griffe, Tastatur-Fokusring, ausgewählte Audio-Quellen) laufen über Signal-Gelb `#FFD21F` / `#E0B83A`.
3. **Klare Hierarchie ohne sinnlose Nummerierung:** Keine Listennummern (`1.`–`5.`) vor Reglern. Die **Direkt-Verstärkung (Haupt-Lautstärke)** sitzt als hervorgehobenes Hero-Steuerelement (`#1F1712`, stärkere Spur, große `Fira Mono`-Anzeige) über den übrigen Reglern.
4. **Vollständige Tastatur-Steuerung vom Sofa aus:** `↑`/`↓` schaltet nahtlos durch alle sichtbaren Regler (5 im Modus Fest, 12 im Modus Dynamisch), `F6` springt in derselben Zeile in die andere Spalte, `←`/`→` ändert den Wert in `1,0 dB`-Schritten (`Shift + ←/→` für `0,1 dB`). Die fokussierte Regler-Zeile erhält einen unübersehbaren gelben Rahmen (`2px solid #FFD21F`), aufgehellte Fläche (`#271C16`) und Fokus-Pfeil `▶`.
5. **Preset-Wahrheit durch Wertabgleich:** Jedes Werk-Preset (`0 dB`, `+5`..`+25 dB`, Kino-Profile mit `β`) und jeder der 6 eigenen Speicherplätze wird automatisch gelb hervorgehoben, sobald Modus und Reglerwerte des gewählten Ziels mit ihm übereinstimmen. Alle drei Reihen (Lautstärke-Stufen, Kino-Profile, Speicherplätze) sind immer sichtbar. Presets und Speicherplätze sind Vorlagen: Ein Klick lädt Modus und Werte ins gewählte Programm.
9. **Zwei Modi, ein Raster:** Der Bearbeitungskopf trägt rechts den Segment-Umschalter `Fest | Dynamisch · Beta` (aktives Segment `#3D2C07` mit gelbem Rand). Die fünf gemeinsamen Regler stehen in beiden Modi am selben Platz (links Direkt-Verstärkung und Stimmen, rechts Bass, Höhen, Stereo-Breite); der Modus Dynamisch blendet die Dynamik-Regler darunter ein, nichts springt. Dynamisch ist überall mit einem kleinen `β`-Etikett (`#3D2C07`, Rand `#E0B83A`) gekennzeichnet, auch in der Programmliste.
10. **Versteckte Lautstärke sichtbar machen:** Senkt PipeWire einen Stream heimlich ab, zeigt die Programmzeile in der zweiten Zeile ein dunkelrotes Mono-Etikett (`-48 dB`, Text `#E8C462`), der Bearbeitungskopf einen Hinweis mit „Zurücksetzen“, und unter dem Banner erscheint eine Leiste (`#2A0E0E`, Rand `#A86B4A`) mit „Lautstärke voll zurücksetzen“.
6. **Erst wählen, dann einstellen:** Links steht die Programm-Seitenleiste (`300 px`, „Standard" fest oben, darunter spielende Programme, dann nach „zuletzt gehört"; Programme, die länger als 7 Tage still waren, eingeklappt unter „Länger still"). Die gewählte Zeile trägt denselben gelben Rahmen wie der Regler-Fokus (`2px solid #FFD21F`, Fläche `#271C16`, `▶` vor dem Namen). Rechts darüber benennt ein Bearbeitungskopf (`#1F1712`, Name in `Fira Sans Condensed` 21 px Gelb) das Ziel und seinen Zustand; „Zurück zu Standard" erscheint nur bei eigener Einstellung.
7. **Marken in der Titelleiste:** Links vor dem Titel das Cinegain-Logo (46 px, aus `cinegain.svg`), rechts nach „Beenden" die Bildmarken von roteson.de (verlinkt) und R²W² Development (je 50 px, `assets/icons/brand-*.png`, ohne Schriftzug, weil der bei dieser Größe unlesbar wäre; Name im Tooltip).
8. **Gezeichnet statt Emoji:** Statuspunkt (gefüllt = spielt, zwei Balken = pausiert, Ring = still), Schalter und Lautstärke-Badge werden gezeichnet bzw. aus Material-Symbol-Pfaden in Palettenfarben gerendert. Zu lange Zeilentexte werden mit „…" gekürzt, der volle Text steht im Tooltip.

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
| `stateWaiting` | `#E8C462` | Status „Pausiert", Lautstärke-Badge |
| `stateIdle` | `#A2968A` | Status „INAKTIV" / „Direkt auf Lautsprecher" |

## Typografie

- **Titel & Sektionsköpfe:** `Fira Sans Condensed` (Bold / SemiBold)
- **Regler-Labels & Buttons:** `Fira Sans` (Medium / SemiBold)
- **Pegel- & Messwerte (`dB`, `:1`):** `Fira Mono` (Bold)
