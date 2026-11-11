# Cinegain — Produktkontext

## Zweck

Cinegain ist ein Desktop- und System-Tray-Werkzeug für Linux (PipeWire) mit zwei Hauptfunktionen (Neuausrichtung 0.5.0, Spezifikation in `docs/SPEC.md`):

1. **Lautstärke über das Systemmaß (Hauptfunktion):** Jedes Programm wird lauter als 100 % Systemlautstärke, von −30 bis +15 dB, mit Overdrive bis +40 dB. Lauter wird mit 10 dB/s angefahren, leiser wirkt sofort. Ein Look-Ahead-Limiter verhindert Übersteuern.
2. **Versteckte Lautstärken sehen, einstellen, zurücksetzen:** PipeWire führt pro Stream eine zweite Lautstärke, die kein Mixer zeigt. WirePlumber stellt sie bei jedem neuen Stream wieder her. Cinegain zeigt sie in einer Analyse-Ansicht samt 7-Tage-Zeitleiste, setzt sie pro Knoten und setzt sie zurück.

Die frühere Filmton-Optimierung (Leveler, Kompressor, Sprachfokus) bleibt als Modus **Dynamisch · Beta**.

## Ansichtsstufen

- **Einfach:** nur lauter oder leiser stellen. Hauptregler, Schnellstufen, Filter an/aus, Overdrive.
- **Erweitert:** alle Regler, Presets und Speicherplätze, versteckte Lautstärken, Analyse, Dynamisch · Beta.
- **Profi:** zusätzlich Rohwerte und das Debug-Log in der App.

## Modi

- **Fest:** feste Lautstärke-Anhebung plus feste Klangfarbe (Stimmen, Bass, Höhen, Stereo-Breite). Keine Signalanalyse.
- **Dynamisch · Beta:** die volle Filmton-Kette. Beta-Funktionen tragen einen gestrichelten Rahmen.
- Jeder Modus hat einen eigenen Wertesatz. Standard und jedes Programm mit eigener Einstellung haben ihren eigenen Modus.

## Nutzungssituation

- **Modus:** `Operate`, also ein Werkzeug zur direkten Klangsteuerung am Schreibtisch oder vom Sofa aus. Dazu kommt Analyse, wenn Programme rätselhaft leise werden.
- **Umgebung:** häufig abends oder im abgedunkelten Raum, oft mit schneller Bedienung per Maus oder ausschließlich per Tastatur (`↑`/`↓`/`←`/`→`, Shift 0,1 dB, Strg 5 dB).
- **Primäre Aktionen:**
  1. Ein Programm (oder Standard) lauter oder leiser stellen: mit Hauptregler, ±-Knöpfen oder Schnellstufen (0 bis +15 dB).
  2. Nachsehen, ob ein Programm heimlich abgesenkt ist, und es zurücksetzen.
  3. Bei Bedarf Klangfarbe oder Dynamisch · Beta feinabstimmen.
