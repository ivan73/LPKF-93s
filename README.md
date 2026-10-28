# LPKF-93s – Fräsdaten für den ProtoMat 93S ohne CircuitCAM

Erzeugt aus Gerber- und Excellon-Dateien (Fusion Electronics, EAGLE, KiCad …) direkt eine
`.LMD`-Datei für **LPKF BoardMaster 5.x**. Der Umweg über CircuitCAM entfällt.

Zwei Wege, gleiche Rechenlogik:

* **Weboberfläche** auf einem Raspberry Pi im Werkstattnetz: CAM-ZIP hochladen, Lagen prüfen,
  Werkzeuge wählen, Vorschau ansehen, LMD herunterladen.
* **Kommandozeile** `gerber2lmd.py` für Skripte und den eigenen Rechner.

## Raspberry Pi einrichten

    git clone https://github.com/ivan73/LPKF-93s.git
    cd LPKF-93s
    sudo ./deploy/install.sh

Getestet mit Shapely 1.8 (Raspberry Pi OS Bookworm) und 2.x.

Danach läuft die Seite unter `http://<IP-des-Pi>:8093` und startet beim Booten automatisch.
Aufträge liegen in `data/` (die letzten 60 werden behalten, `LPKF_KEEP` ändert das).

Aktualisieren: `git pull && sudo systemctl restart lpkf-93s`

### Ablauf auf der Seite

1. In Fusion *CAM-Daten erzeugen* und das ZIP auf die Seite ziehen (oder einzelne Gerber/Excellon-Dateien).
2. Die Zuordnung prüfen. Kupfer Top/Bottom, Umriss und Bohrungen werden automatisch erkannt,
   und zwar über Gerber-X2-Attribute, EAGLE-Ebenennamen und Dateinamen. Werkzeuge wählen.
3. **LMD erzeugen**. Die Seite rechnet, prüft das Ergebnis selbst und zeigt die Fräsbahnen.
   Nur bei „Prüfung bestanden“ herunterladen und in BoardMaster über *Datei → Import → LMD/LPR* laden.

## Kommandozeile

    sudo apt install python3-shapely python3-matplotlib      # oder: pip install -r requirements.txt

    python3 gerber2lmd.py --top copper_top.gbr --bottom copper_bottom.gbr --outline profile.gbr \
        --drill drill_1_16.xln -o platine.LMD --preview platine.png --verify

    python3 verify.py platine.LMD --top … --bottom … --outline … --drill …   # Prüfung einzeln
    python3 lmdinfo.py platine.LMD                                         # Inhalt anzeigen

Einseitig: nur `--bottom` (oder nur `--top`). Nicht durchkontaktierte Bohrungen: `--drill-npth`.

| Option | Standard | Bedeutung |
|---|---|---|
| `--iso-tool` | `Universal Cutter 0.2 mm (8 mil)` | Isolierfräser (Name wie in der BoardMaster-Bibliothek) |
| `--passes` / `--overlap` | 1 / 0.3 | Anzahl Isolationskanäle, Überlappung |
| `--rubout-tool` | `End Mill 1.0 mm (39 mil)` | Freie Flächen ausräumen, `""` = aus |
| `--cut-tool` | `End Mill 2.0 mm (79 mil) long` | Außenkontur |
| `--tabs` / `--tab-width` | 0 / 1.0 mm | Haltestege mittig auf den längsten Kanten |
| `--hole-router` | `Contour Router 2.0 mm (79 mil)` | Bohrungen > 2,4 mm werden kreisförmig gefräst |
| `--no-marking` | – | Bohrungen nicht vorher ankörnen |

## Was in der LMD landet

| BoardMaster-Phase | Inhalt |
|---|---|
| MillingTop / MillingBottom | Isolation + Ausräumen, Bottom **ungespiegelt** (wie CircuitCAM, BoardMaster spiegelt) |
| DrillingPlated / DrillingUnplated | Jede Bohrung bekommt den nächstliegenden Spiralbohrer 0,2–2,4 mm, größere Löcher werden gefräst |
| MarkingDrills | Ankörnen aller Bohrungen mit 0,2 mm |
| CuttingOutside | Außenkontur, Fräsermitte um den Radius nach außen versetzt, gegen den Uhrzeigersinn |

Phasen- und Werkzeugtabelle stammen aus einer CircuitCAM-4.0-Datei der LBS Meran
(`lpkf93s/template.json`), damit die Namen zur BoardMaster-Werkzeugbibliothek passen.

## Sicherheitsprüfungen

* Warnung, wenn zwei Kupferflächen enger liegen als der Isolierfräser breit ist. Dort ist keine Trennung möglich.
* Isolationsbahnen werden je Kupferfläche berechnet. Bei Abstand = Fräserdurchmesser (z. B. 0,2 mm / 8 mil)
  bleibt die Trennbahn deshalb erhalten.
* Die Prüfung (`verify.py`, in der Weboberfläche automatisch) rechnet unabhängig nach:
  * Kein Fräser schneidet ins Kupfer.
  * Keine Restkupferfläche verbindet zwei Kupferflächen, es gibt also keinen Kurzschluss.
  * Alle Bohrungen sind vorhanden.

## Aufbau

    lpkf93s/core.py      Fräsbahnen berechnen, LMD zusammenstellen
    lpkf93s/gerber.py    Gerber-RS-274X- und Excellon-Leser
    lpkf93s/lmdio.py     LMD lesen/schreiben
    lpkf93s/detect.py    Lagenerkennung für Uploads
    lpkf93s/check.py     unabhängige Prüfung
    lpkf93s/preview.py   PNG-Vorschau
    web/                 Flask-Weboberfläche
    deploy/              Installationsskript und systemd-Dienst
    docs/LMD-Format.md   Beschreibung des LMD-Formats

## Vor dem ersten echten Fräsen

Die erzeugten Dateien entsprechen formal den CircuitCAM-Ausgaben: Der Writer reproduziert eine CircuitCAM-LMD
byte-genau. Auf der Maschine sind sie aber noch nicht erprobt. Deshalb zuerst in BoardMaster importieren,
alle Phasen ansehen und einen Luftlauf (Z oben) oder ein Reststück fräsen.
