"""Unabhängige Prüfung einer LMD-Datei gegen die Gerber-/Excellon-Daten."""
from shapely.geometry import LineString, Point
from shapely.ops import unary_union
from .compat import Index
from . import gerber, lmdio
from .preview import paths


def verify(lmd_bytes, top=None, bottom=None, outline=None, drills=()):
    """Liefert (ok, zeilen). drills = Liste von Excellon-Texten."""
    _, phases = lmdio.read(lmd_bytes)
    ok = True; out = []
    board = None
    if outline:
        board, _ = gerber.outline_polygon(gerber.read_gerber(outline)[1])
    for side, txt, ph_name in (('Top', top, 'MillingTop'), ('Bottom', bottom, 'MillingBottom')):
        if not txt: continue
        cu, _ = gerber.read_gerber(txt)
        cutlist = []
        for p in phases:
            if p['phase'] != ph_name or not p['cmds']: continue
            dia = {t[0]: t[2] * 1e3 for t in p['tools']}
            name = {t[0]: t[3] for t in p['tools']}
            pls, _ = paths(p['cmds'])
            worst = {}
            for t, pts in pls:
                g = LineString([(x * 1e3, y * 1e3) for x, y in pts]) if len(pts) > 1 else Point(pts[0][0] * 1e3, pts[0][1] * 1e3)
                cutlist.append(g.buffer(dia[t] / 2, 8))
                worst[t] = min(worst.get(t, 9), cu.distance(g) - dia[t] / 2)
            what = {'InsulateTop': 'Isolation', 'InsulateBottom': 'Isolation'}.get(p['layer'], p['layer'])
            for t, w in worst.items():
                good = w > -0.002
                ok &= good
                if good:
                    txt = '%s, %s mit %s: kein Kupfer angefräst (min. %.2f mm Luft)' % (side, what, name[t], max(w, 0))
                else:
                    txt = '%s, %s mit %s: fräst %.2f mm ins Kupfer' % (side, what, name[t], -w)
                out.append((good, txt))
        if board is not None:
            rest = board.difference(unary_union(cutlist)) if cutlist else board
            isl = list(getattr(cu, 'geoms', [cu]))
            tree = Index(isl); shorts = []
            for part in getattr(rest, 'geoms', [rest]):
                hit = [j for j in tree.query(part) if isl[j].intersection(part).area > 1e-4]
                if len(hit) > 1:
                    q = part.representative_point(); shorts.append((q.x, q.y, len(hit)))
            for x, y, n in shorts[:10]:
                out.append((False, '%s: Kurzschluss – %d Kupferflächen bleiben verbunden bei X=%.2f Y=%.2f' % (side, n, x, y)))
            out.append((not shorts, ('%s: alle %d Kupferflächen sauber voneinander getrennt' % (side, len(isl))) if not shorts else ('%s: %d ungetrennte Stellen' % (side, len(shorts)))))
            ok &= not shorts
    if drills:
        holes = [h for d in drills for h in gerber.read_excellon(d)]
        found = []
        for p in phases:
            if p['phase'] not in ('DrillingPlated', 'DrillingUnplated'): continue
            pls, dr = paths(p['cmds'])
            found += [(x * 1e3, y * 1e3) for t, (x, y) in dr]
            found += [(pts[0][0] * 1e3, pts[0][1] * 1e3) for t, pts in pls]
        miss = [h for h in holes if not any(abs(h[1] - x) < 0.01 and abs(h[2] - y) < 0.01 for x, y in found)]
        good = not miss and len(found) == len(holes)
        ok &= good
        out.append((good, ('Bohrungen: alle %d vorhanden' % len(holes)) if good else ('Bohrungen: %d in Excellon, %d in LMD, %d fehlen' % (len(holes), len(found), len(miss)))))
    return ok, out
