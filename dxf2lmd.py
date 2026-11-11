#!/usr/bin/env python3
"""dxf2lmd – LPKF-LMD-Fräsdaten aus einer DXF-Zeichnung (AutoCAD & Co.), z.B. für Frontplatten.

Beispiele:
  python3 dxf2lmd.py frontplatte.dxf -o FRONT.LMD --list
  python3 dxf2lmd.py frontplatte.dxf -o FRONT.LMD --layer Kontur=outer --layer Ausbrueche=cutout \\
      --layer Gravur=engrave --layer Bohrungen=drill --tabs 2 --preview front.png

Bearbeitung je Layer (--layer NAME=OP): auto, outer (Außenkontur), cutout (Ausschnitt),
engrave (Gravur, Fräsermitte auf der Linie), drill (Kreise bohren), ignore.
Ohne Angabe gilt auto: kleine Kreise bohren, größte geschlossene Kontur = Außenkontur,
geschlossene Konturen darin = Ausschnitte, offene Linien und Texte gravieren.
"""
import argparse, sys
from lpkf93s import core, dxf, preview


def main():
    d = core.Options()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dxf')
    ap.add_argument('-o', '--output')
    ap.add_argument('--list', action='store_true', help='Layer mit Inhalt anzeigen')
    ap.add_argument('--layer', action='append', default=[], metavar='NAME=OP')
    ap.add_argument('--unit', choices=['auto', 'mm', 'inch', 'cm'], default='auto',
                    help='Einheit der Zeichnung (auto = $INSUNITS der Datei, fehlt sie: mm)')
    ap.add_argument('--cut-tool', default=d.cut_tool, help='Außenkontur (CuttingOutside)')
    ap.add_argument('--cut-in-tool', default=d.cut_in_tool, help='Ausschnitte (CuttingInside)')
    ap.add_argument('--engrave-tool', default=d.engrave_tool)
    ap.add_argument('--tabs', type=int, default=d.tabs)
    ap.add_argument('--tab-width', type=float, default=d.tab_width)
    ap.add_argument('--no-marking', action='store_true')
    ap.add_argument('--mark-tool', default=d.mark_tool)
    ap.add_argument('--user', default=d.user)
    ap.add_argument('--preview', help='PNG-Vorschau schreiben')
    a = ap.parse_args()

    if a.list:
        s = dxf.summary(a.dxf, unit=a.unit)
        print('DXF %s, Einheit %s (Faktor %g → mm), Größe %.2f x %.2f mm' % (s['info']['version'], s['info']['units'],
                                                                         s['info']['factor'], *s['info']['size']))
        for l in s['layers']:
            print('  %-24s geschlossen %3d  offen %3d  Kreise %3d (bohrbar %d)%s' % (
                l['name'], l['closed'], l['open'], l['circles'], l['drillable'], '  Text' if l['text'] else ''))
        if not a.output:
            return 0
    if not a.output:
        ap.error('-o/--output fehlt')
    ops = {}
    for x in a.layer:
        n, _, op = x.rpartition('=')
        if op not in dxf.OPS or not n:
            ap.error('--layer %s: erwartet NAME=%s' % (x, '|'.join(dxf.OPS)))
        ops[n] = op
    opt = core.Options(cut_tool=a.cut_tool, cut_in_tool=a.cut_in_tool, engrave_tool=a.engrave_tool, tabs=a.tabs,
                       tab_width=a.tab_width, marking=not a.no_marking, mark_tool=a.mark_tool, user=a.user)
    res, checks = dxf.generate(a.dxf, opt, ops, unit=a.unit)
    with open(a.output, 'wb') as f:
        f.write(res.lmd)
    print('\n'.join(res.log))
    for g, t, _ in checks:
        print('  %s %s' % ('ok ' if g else '!! ', t))
    if a.preview:
        with open(a.preview, 'wb') as f:
            f.write(preview.render(res.lmd, res.board, only='MillingTop'))
    print('geschrieben: %s (%d Bytes)' % (a.output, len(res.lmd)))
    return 0 if all(g for g, _, _ in checks) else 2


if __name__ == '__main__':
    sys.exit(main())
