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
2. Die Zuordnung prüfen. Kupfer Top/Bottom, Umriss, Bohrungen und Lötstoppmasken werden automatisch erkannt,
   und zwar über Gerber-X2-Attribute, EAGLE-Ebenennamen und Dateinamen. Werkzeuge wählen.
3. **LMD erzeugen**. Die Seite rechnet, prüft das Ergebnis selbst und zeigt die Fräsbahnen.
   Nur bei „Prüfung bestanden“ herunterladen und in BoardMaster über *Datei → Import → LMD/LPR* laden.

### Bohrer je Durchmesser (z. B. Vias für Drahtbrücken)

In Schritt 2 listet die Seite jeden Bohrdurchmesser aus der Excellon-Datei mit Anzahl, Art und vorgeschlagenem Bohrer.
Vias erkennt sie an Excellon-Attributen (`ViaDrill`, z. B. KiCad), sonst geschätzt am Durchmesser (≤ 0,7 mm, „Via?“).
Für jeden Durchmesser lässt sich ein anderer Bohrer wählen, z. B. 0,61-mm-Vias mit 0,8 mm bohren, wenn sie mit
Drahtbrücken statt Nieten durchkontaktiert werden. Der **Restring** wird dabei live angezeigt, also der Abstand
zwischen Bohrung und Außenrand des Pads:

* grün: ab 0,15 mm, ausreichend
* gelb: unter 0,15 mm, das Lötauge kann abreißen
* rot: unter 0,05 mm, das Pad ist praktisch weggebohrt

Wer Vias regelmäßig mit Draht lötet, stellt am besten gleich im Layout einen größeren Via-Durchmesser ein.
Die Auswahl wird je Durchmesser gemerkt. Kommandozeile: `--drill-map "0.61=Spiral Drill 0.8 mm"`.

### Ankörnen

Standardwerkzeug ist `Universal Cutter 0.2 mm (8 mil)`, also derselbe Fräser wie beim Isolieren.
Alternativ ist `Universal Cutter 0.2 mm marking` wählbar. Kommandozeile: `--mark-tool`.

### Reihenfolge prüfen (Ablauf)

Unter der Vorschau gibt es neben „Bestückungsseite“ und „Lötseite“ den Reiter **Ablauf**. Er zeigt die Bahnen
und Bohrungen einer Phase genau in der Reihenfolge, in der sie in der LMD stehen:

* **Abspielen:** abspielen, schrittweise vor und zurück (Pfeiltasten, Leertaste), Schieberegler zum Springen
* **Darstellung:** gefräst grün, aktueller Schritt kupferfarben mit Absenkpunkt, Leerwege gestrichelt, Offenes grau
* **Anzeige:** Werkzeug, Werkzeugwechsel, Koordinaten der Bohrung und Leerweg bis hier bzw. gesamt
* **Ansicht:** Mausrad zoomt, Ziehen verschiebt, Doppelklick setzt zurück

### Geschätzte Fräszeit

Im Ergebnis steht je Phase eine Zeitschätzung mit Schnittweg, Absenkungen, Bohrungen und Werkzeugwechseln.
Der Ablauf-Player zeigt zusätzlich die Zeit bis zum aktuellen Schritt. Grundlage sind die **Maschinenwerte**
im aufklappbaren Bereich darunter:

| Wert | Richtwert | Herkunft |
|---|---|---|
| Je Bohrung / Ankörnpunkt, Absenken + Heben | 1 s | 93s-Handbuch: max. 60 Hübe/min |
| Vorschub Konturfräser | 2 mm/s | 93s-Handbuch, Konturfräsen |
| Vorschub Isolier- / Ausräumfräser | 10 / 8 mm/s | Annahme |
| Leerfahrt | 30 mm/s | Annahme |
| Werkzeugwechsel | 60 s | Annahme (manueller Wechsel) |

Nach dem ersten echten Lauf am besten die Vorschübe aus der BoardMaster-Werkzeugbibliothek eintragen.
Die Werte merkt sich der Browser.

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
| `--iso-width` | 0 (= Fräserbreite) | Isolationsbreite in mm; daraus wird die Kanalzahl berechnet |
| `--overlap` | 0.3 | Mindestüberlappung benachbarter Kanäle (30 %) |
| `--passes` | 1 | feste Kanalzahl, nur ohne `--iso-width` |
| `--pad-width` | 0 (aus) | Pad-Freistellung: um Lötpads so breit freifräsen (mm) |
| `--mask-top` / `--mask-bottom` | – | Lötstoppmaske, daraus werden die Pads erkannt |
| `--rubout-tool` | `End Mill 1.0 mm (39 mil)` | Freie Flächen ausräumen, `""` = aus |
| `--cut-tool` | `End Mill 2.0 mm (79 mil) long` | Außenkontur |
| `--tabs` / `--tab-width` | 0 / 1.0 mm | Haltestege mittig auf den längsten Kanten |
| `--hole-router` | `Contour Router 2.0 mm (79 mil)` | Bohrungen > 2,4 mm werden kreisförmig gefräst |
| `--no-marking` | – | Bohrungen nicht vorher ankörnen |

### Isolationsbreite und Überlappung

Aus Breite *W*, Fräser *d* und Mindestüberlappung *ü* ergibt sich die Kanalzahl
*n* = 1 + ⌈(W − d) / (d · (1 − ü))⌉. Die Kanäle werden gleichmäßig verteilt, sodass die Breite genau stimmt.
Die tatsächliche Überlappung kann daher größer sein als eingestellt. Beispiele mit 0,2-mm-Fräser und 30 %:

| Breite | Kanäle | Versatz | tatsächliche Überlappung |
|---|---|---|---|
| 0,2 mm | 1 | – | – |
| 0,34 mm | 2 | 0,14 mm | 30 % |
| 0,4 mm | 3 | 0,10 mm | 50 % |
| 1,0 mm | 7 | 0,133 mm | 33 % |

Wo zwischen zwei Kupferflächen weniger Platz ist, entfallen die äußeren Kanäle dort automatisch.
Die Weboberfläche zeigt die Rechnung beim Eintippen live an.

### Pad-Freistellung

Gefräste Platinen haben meist keinen Lötstopplack. Damit beim Löten kein Zinn überzieht, kann um die Lötpads
breiter freigefräst werden als an den Leiterbahnen, z. B. Isolation 0,4 mm und Pads 0,8 mm.
Die Pads werden aus der **Lötstoppmaske** erkannt (bedrahtet und SMD; Fusion/EAGLE exportieren sie mit,
z. B. `.stc`/`.sts` oder `soldermask_top.gbr`). Fehlt sie, dienen die Bohrungen als Ersatz (nur bedrahtete Pads).
Die zusätzlichen Ringe werden überall gekappt, wo sie Kupfer berühren würden, also auch die Leiterbahn am Pad
und benachbarte Masseflächen. Breiter als der Abstand im Layout (z. B. Polygon-*Isolate* in EAGLE) wird es daher nicht.

## Was in der LMD landet

| BoardMaster-Phase | Inhalt |
|---|---|
| MillingTop / MillingBottom | Isolation + Ausräumen, Bottom **ungespiegelt** (wie CircuitCAM, BoardMaster spiegelt) |
| DrillingPlated / DrillingUnplated | Jede Bohrung bekommt den nächstliegenden Spiralbohrer 0,2–2,4 mm, größere Löcher werden gefräst |
| MarkingDrills | Ankörnen aller Bohrungen mit 0,2 mm |
| CuttingOutside | Außenkontur, Fräsermitte um den Radius nach außen versetzt, gegen den Uhrzeigersinn |

Phasen- und Werkzeugtabelle stammen aus einer CircuitCAM-4.0-Datei der LBS Meran
(`lpkf93s/template.json`), damit die Namen zur BoardMaster-Werkzeugbibliothek passen.

### Problemstellen anzeigen

Warnungen (z. B. „Abstand 0,152 mm < Fräser 0,20 mm“) und Prüffehler (Kurzschluss, Kupfer angefräst, fehlende Bohrung)
sind im Ergebnis anklickbar. Ein Klick öffnet die Vektoransicht der richtigen Seite mit allen Bahnen und dem Kupfer,
zoomt auf einen Ausschnitt von etwa 15 mm und markiert die Stelle; weitere Problemstellen derselben Seite erscheinen blass, die angeklickte Meldung ist in der Liste hervorgehoben. Bei Kurzschlüssen wird die engste Stelle zwischen den verbundenen
Kupferflächen gezeigt, also genau dort, wo der Fräser nicht durchkommt.

Abhilfe bei zu engen Abständen: einen feineren Isolierfräser wählen (z. B. `End Mill (RF) 0.15 mm`,
`Micro Cutter 0.13 mm` oder `0.1 mm`) oder im Layout den Mindestabstand vergrößern.

## Wegoptimierung

* **Reihenfolge:** Erst wird jeweils die nächstgelegene Bahn bzw. Bohrung gewählt. Danach optimiert ein 2-opt-Verfahren
  nach, das Teilfolgen umdreht und offene Bahnen wendet. Gestartet wird an der linken unteren Platinenecke.
* **Einstieg:** Geschlossene Ringe beginnen an dem Punkt, der zwischen Vorgänger und Nachfolger am günstigsten liegt.
* **Weniger Absenkungen:** Liegt das Ende einer Bahn höchstens 1 mm vom Anfang der nächsten entfernt und berührt
  die Verbindungsstrecke kein Kupfer, bleibt der Fräser unten. Heben und Senken kostet an der 93S mehr Zeit
  als ein kurzer Vorschubweg.
* **Rechenzeit:** Die Nachoptimierung ist je Arbeitsschritt auf 2 s begrenzt. Auf langsamen Rechnern wird sie
  dadurch etwas weniger gründlich, aber nie langsamer.

Bei der ldatemMAP-Platine (Isolation 0,4 mm, Pad-Freistellung 0,8 mm) ergibt das rund 40 % weniger Absenkungen und
gut 15 % weniger Leerweg als die einfache Reihenfolge.

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
