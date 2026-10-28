#!/usr/bin/env python3
"""gerber2lmd – LPKF-LMD-Fräsdaten (BoardMaster 5.x) direkt aus Gerber/Excellon, ohne CircuitCAM.

Beispiel (EAGLE/Fusion, doppelseitig):
  python3 gerber2lmd.py --top car.cmp --bottom car.sol --outline car.bor --drill drills.drd \\
      -o car.LMD --preview car.png
Einseitig: nur --bottom (oder nur --top).
"""
import argparse, sys
from lpkf93s import core, preview, check


def main():
    d = core.Options()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--top'); ap.add_argument('--bottom')
    ap.add_argument('--outline', required=True)
    ap.add_argument('--drill', action='append', default=[], help='Excellon, durchkontaktiert (mehrfach möglich)')
    ap.add_argument('--drill-npth', action='append', default=[], help='Excellon, nicht durchkontaktiert')
    ap.add_argument('--mask-top', help='Lötstoppmaske Top (erkennt Pads für --pad-width)')
    ap.add_argument('--mask-bottom', help='Lötstoppmaske Bottom')
    ap.add_argument('--pad-width', type=float, default=d.pad_width,
                    help='Pad-Freistellung: um Lötpads so breit freifräsen (mm), 0 = aus')
    ap.add_argument('-o', '--output', required=True)
    ap.add_argument('--template', default=core.TEMPLATE)
    ap.add_argument('--iso-tool', default=d.iso_tool)
    ap.add_argument('--iso-width', type=float, default=d.iso_width,
                    help='Isolationsbreite in mm (ergibt die Kanalzahl); 0 = --passes verwenden')
    ap.add_argument('--passes', type=int, default=d.passes, help='Anzahl Kanäle, wenn keine --iso-width')
    ap.add_argument('--overlap', type=float, default=d.overlap, help='Mindestüberlappung der Kanäle (0..0.9)')
    ap.add_argument('--rubout-tool', default=d.rubout_tool, help='"" = kein Ausräumen')
    ap.add_argument('--rubout-overlap', type=float, default=d.rubout_overlap)
    ap.add_argument('--cut-tool', default=d.cut_tool)
    ap.add_argument('--tabs', type=int, default=d.tabs)
    ap.add_argument('--tab-width', type=float, default=d.tab_width)
    ap.add_argument('--hole-router', default=d.hole_router)
    ap.add_argument('--no-marking', action='store_true')
    ap.add_argument('--user', default=d.user)
    ap.add_argument('--preview', help='PNG-Vorschau')
    ap.add_argument('--verify', action='store_true', help='Ergebnis prüfen (Kupferabstand, Kurzschluss, Bohrungen)')
    a = ap.parse_args()

    rd = lambda f: open(f, encoding='latin1').read() if f else None
    inp = core.Inputs(top=rd(a.top), bottom=rd(a.bottom), outline=rd(a.outline),
                      drills=[(rd(f), True) for f in a.drill] + [(rd(f), False) for f in a.drill_npth],
                      mask_top=rd(a.mask_top), mask_bottom=rd(a.mask_bottom))
    opt = core.Options(iso_tool=a.iso_tool, iso_width=a.iso_width, pad_width=a.pad_width, passes=a.passes, overlap=a.overlap, rubout_tool=a.rubout_tool,
                       rubout_overlap=a.rubout_overlap, cut_tool=a.cut_tool, tabs=a.tabs, tab_width=a.tab_width,
                       hole_router=a.hole_router, marking=not a.no_marking, user=a.user)
    res = core.generate(inp, opt, a.template)
    print('\n'.join(res.log))
    open(a.output, 'wb').write(res.lmd)
    print('Geschrieben:', a.output)
    if a.preview:
        open(a.preview, 'wb').write(preview.render(res.lmd, res.board)); print('Vorschau:', a.preview)
    if a.verify:
        ok, lines = check.verify(res.lmd, inp.top, inp.bottom, inp.outline, [t for t, _ in inp.drills])
        for good, l in lines: print('OK    ' if good else 'FEHLER', l)
        print('PRÜFUNG:', 'alles in Ordnung' if ok else 'PROBLEME gefunden')
        sys.exit(0 if ok else 2)


if __name__ == '__main__':
    main()
