# Cinegain — Gestaltung (CI-Designsystem Rot-Gelb-Schwarz)

> Verbindliche visuelle Richtung, abgeleitet aus `/vault/kontext/ci-designsystem.md` und `boksy2/DESIGN.md` (Red Star #1, Nachtnutzungs-Variante).

## Leitlinien

1. **Plakativ, technisch nüchtern, sofort ablesbar:** Rote Bannerfläche mit gelber Condensed-Typografie, warme dunkle Panels mit dunkelroten Rändern, keine generische Blau/Grau-Dashboard-Optik.
2. **Rot ist Marke und Ausschalt-/Alarmzustand, Gelb ist Aktion:** Das Titelbanner trägt `#B11218` mit `#FFD21F`. Alle interaktiven Bedienelemente (aktive Presets, Slider-Füllungen, Griffe, Tastatur-Fokusring, ausgewählte Audio-Quellen) laufen über Signal-Gelb `#FFD21F` / `#E0B83A`.
3. **Klare Hierarchie ohne sinnlose Nummerierung:** Keine Listennummern (`1.`–`5.`) vor Reglern. Die **Direkt-Verstärkung (Haupt-Lautstärke)** sitzt als hervorgehobenes Hero-Steuerelement (`#1F1712`, stärkere Spur, große `Fira Mono`-Anzeige) über den übrigen Reglern.
4. **Vollständige Tastatur-Steuerung vom Sofa aus:** `↑`/`↓` schaltet nahtlos durch alle sichtbaren Regler (1 in Einfach, 5 im Modus Fest, 12 im Modus Dynamisch), `F6` springt in derselben Zeile in die andere Spalte, `←`/`→` ändert den Wert in `1 dB`-Schritten (`Shift` `0,1 dB`, `Strg` `5 dB`). Rechts neben jedem Regler sitzen `−`/`+` (`QToolButton#step_btn`, 26 px, Mono, Rand `#4A251D`, gedrückt Gelb) mit denselben Schritten und Auto-Repeat; sie nehmen keinen Fokus. Rechtsklick auf einen Regler setzt ihn neutral. Die fokussierte Regler-Zeile erhält einen unübersehbaren gelben Rahmen (`2px solid #FFD21F`), aufgehellte Fläche (`#271C16`) und Fokus-Pfeil `▶`.
5. **Preset-Wahrheit durch Wertabgleich:** Jedes Werk-Preset (`0 dB`, `+3`..`+15 dB`, Kino-Profile gestrichelt) und jeder der 6 eigenen Speicherplätze wird automatisch gelb hervorgehoben, sobald Modus und Reglerwerte des gewählten Ziels mit ihm übereinstimmen. Ab Erweitert sind alle drei Reihen (Lautstärke-Stufen, Kino-Profile, Speicherplätze) sichtbar, in Einfach nur die Lautstärke-Stufen. Presets und Speicherplätze sind Vorlagen: Ein Klick lädt Modus und Werte ins gewählte Programm.
9. **Zwei Modi, ein Raster:** Der Bearbeitungskopf trägt rechts den Segment-Umschalter `Fest | Dynamisch · Beta` (aktives Segment `#3D2C07` mit gelbem Rand). Die fünf gemeinsamen Regler stehen in beiden Modi am selben Platz (links Direkt-Verstärkung und Stimmen, rechts Bass, Höhen, Stereo-Breite); der Modus Dynamisch blendet die Dynamik-Regler darunter ein, nichts springt. Beta wird nicht mit einem Symbol, sondern mit einem gestrichelten Rahmen (`1px dashed #E0B83A`) gezeigt: Segment „Dynamisch · Beta“, Film-Presets, dynamische Speicherplätze und das Etikett „Dynamisch“ in der Programmliste. Ein Tooltip nennt es „Beta-Funktion“.
10. **Versteckte Lautstärke sichtbar machen:** Ab Erweitert nennt der Bearbeitungskopf die versteckte Lautstärke immer (bei 0 dB ruhig in `#A2968A`). Senkt PipeWire einen Stream heimlich ab, zeigt die Programmzeile in der zweiten Zeile ein dunkelrotes Mono-Etikett (`-48 dB`, Text `#E8C462`), der Kopf den Wert in `#E8C462` mit „Zurücksetzen“, und unter dem Banner erscheint eine Leiste (`#2A0E0E`, Rand `#A86B4A`) mit „Lautstärke voll zurücksetzen“. Der Tab „Analyse“ zeigt alle Knoten als Tabelle (abgesenkte Zeilen `#2A0E0E` mit `#E8C462`, Soundkarten gedimmt `#A2968A` und „nur lesend“) und darunter die Zeitleiste der letzten 7 Tage.
11. **Ansichtsstufen in der Titelleiste:** Segment `Einfach | Erweitert | Profi` (`QPushButton#view_seg`, aktiv `#3D2C07` mit gelbem Rand). Einfach blendet alles bis auf Hauptregler, Schnellstufen, Filter und Overdrive aus; ein freier Abstand unten ersetzt das leere Panel. Rechts im Inhalt gibt es Tabs `Klang | Analyse | Log` (Log nur in Profi), in Einfach ohne Tableiste.
12. **Overdrive in Orange:** Orange `#FF8A1F` ist nur für Overdrive reserviert. Der Knopf in der Titelleiste ist aus mit orangem Rand, an orange gefüllt mit „OVERDRIVE AN“. Unter der Reglerspur markiert eine 3 px-Linie den Bereich über +15 dB, Werte darüber erscheinen orange. Der Warndialog hat einen orangen Rahmen, „Einschalten“ wird erst nach dem Haken orange und klickbar, der Fokus liegt auf „Abbrechen“. Während die Lautstärke noch hochfährt, steht im Hauptregler „fährt hoch … noch X dB“ in Orange.
13. **Verstellbare Programmliste:** ein ziehbarer Trenner (10 px, beim Hover `#3A2A22`), Startbreite 280 px, mindestens 200 px, höchstens die halbe Fensterbreite. Ein Doppelklick setzt die Breite zurück.
6. **Erst wählen, dann einstellen:** Links steht die Programm-Seitenleiste (`280 px`, verstellbar, „Standard" fest oben, darunter spielende Programme, dann nach „zuletzt gehört"; Programme, die länger als 7 Tage still waren, eingeklappt unter „Länger still"). Die gewählte Zeile trägt denselben gelben Rahmen wie der Regler-Fokus (`2px solid #FFD21F`, Fläche `#271C16`, `▶` vor dem Namen). Rechts darüber benennt ein Bearbeitungskopf (`#1F1712`, Name in `Fira Sans Condensed` 21 px Gelb) das Ziel und seinen Zustand; „Zurück zu Standard" erscheint nur bei eigener Einstellung.
7. **Marken in der Titelleiste:** Links vor dem Titel das Cinegain-Logo (46 px, aus `cinegain.svg`), rechts nach „Beenden" die Bildmarken von roteson.de (verlinkt) und R²W² Development (verlinkt auf das GitHub-Projekt; je 50 px, `assets/icons/brand-*.png`, ohne Schriftzug, weil der bei dieser Größe unlesbar wäre; Name im Tooltip).
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
| `overdrive` | `#FF8A1F` | Nur Overdrive: Knopf, Reglerzone über +15 dB, Werte darüber, Rampenhinweis, Warndialog |

## Typografie

- **Titel & Sektionsköpfe:** `Fira Sans Condensed` (Bold / SemiBold)
- **Regler-Labels & Buttons:** `Fira Sans` (Medium / SemiBold)
- **Pegel- & Messwerte (`dB`, `:1`):** `Fira Mono` (Bold)
