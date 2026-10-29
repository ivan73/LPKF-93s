"""Unabhängige Prüfung einer LMD-Datei gegen die Gerber-/Excellon-Daten."""
from shapely.geometry import LineString, Point
from shapely.ops import unary_union, nearest_points
from .compat import Index
from . import gerber, lmdio
from .preview import paths


def verify(lmd_bytes, top=None, bottom=None, outline=None, drills=(), progress=None):
    """Liefert (ok, zeilen). drills = Liste von Excellon-Texten.
    Jede Zeile: (gut, text, ort) – ort = dict(side, x, y) oder None."""
    _, phases = lmdio.read(lmd_bytes)
    prog = progress or (lambda f, t: None)
    ok = True; out = []
    board = None
    if outline:
        board, _ = gerber.outline_polygon(gerber.read_gerber(outline)[1])
    for side, txt, ph_name in (('Top', top, 'MillingTop'), ('Bottom', bottom, 'MillingBottom')):
        if not txt: continue
        prog(0.05 if side == 'Top' else 0.45, 'Prüfung %s: Kupfer angefräst?' % side)
        cu, _ = gerber.read_gerber(txt)
        cutlist = []
        for p in phases:
            if p['phase'] != ph_name or not p['cmds']: continue
            dia = {t[0]: t[2] * 1e3 for t in p['tools']}
            name = {t[0]: t[3] for t in p['tools']}
            pls, _ = paths(p['cmds'])
            worst = {}; where = {}
            for t, pts in pls:
                g = LineString([(x * 1e3, y * 1e3) for x, y in pts]) if len(pts) > 1 else Point(pts[0][0] * 1e3, pts[0][1] * 1e3)
                cutlist.append(g.buffer(dia[t] / 2, 8))
                dd = cu.distance(g) - dia[t] / 2
                if dd < worst.get(t, 9):
                    worst[t] = dd; where[t] = g
            what = {'InsulateTop': 'Isolation', 'InsulateBottom': 'Isolation'}.get(p['layer'], p['layer'])
            for t, w in worst.items():
                good = w > -0.002
                ok &= good
                loc = None
                if good:
                    txt = '%s, %s mit %s: kein Kupfer angefräst (min. %.2f mm Luft)' % (side, what, name[t], max(w, 0))
                else:
                    a, b = nearest_points(where[t], cu)
                    loc = dict(side=side, x=round(a.x, 3), y=round(a.y, 3))
                    txt = '%s, %s mit %s: fräst %.3f mm ins Kupfer bei X=%.2f Y=%.2f' % (side, what, name[t], -w, a.x, a.y)
                out.append((good, txt, loc))
        if board is not None:
            prog(0.25 if side == 'Top' else 0.65, 'Prüfung %s: Kurzschlüsse?' % side)
            rest = board.difference(unary_union(cutlist)) if cutlist else board
            isl = list(getattr(cu, 'geoms', [cu]))
            tree = Index(isl); shorts = []
            for part in getattr(rest, 'geoms', [rest]):
                hit = [j for j in tree.query(part) if isl[j].intersection(part).area > 1e-4]
                if len(hit) > 1:
                    # Ort: engste Stelle zwischen zwei der verbundenen Kupferflächen (dort fehlt die Trennung)
                    best = None
                    for ii in range(len(hit)):
                        for jj in range(ii + 1, len(hit)):
                            pa, pb = nearest_points(isl[hit[ii]], isl[hit[jj]])
                            dd = pa.distance(pb)
                            if best is None or dd < best[0]:
                                best = (dd, (pa.x + pb.x) / 2, (pa.y + pb.y) / 2)
                    shorts.append((best[1], best[2], len(hit), best[0]))
            for x, y, n, gap in shorts[:20]:
                out.append((False, '%s: Kurzschluss – %d Kupferflächen bleiben verbunden, engste Stelle %.3f mm bei X=%.2f Y=%.2f'
                            % (side, n, gap, x, y), dict(side=side, x=round(x, 3), y=round(y, 3))))
            out.append((not shorts, ('%s: alle %d Kupferflächen sauber voneinander getrennt' % (side, len(isl))) if not shorts else ('%s: %d ungetrennte Stellen' % (side, len(shorts))), None))
            ok &= not shorts
    if drills:
        prog(0.9, 'Prüfung: Bohrungen vollständig?')
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
        out.append((good, ('Bohrungen: alle %d vorhanden' % len(holes)) if good else ('Bohrungen: %d in Excellon, %d in LMD, %d fehlen' % (len(holes), len(found), len(miss))), None))
        for h in miss[:20]:
            out.append((False, 'Bohrung Ø%.2f fehlt bei X=%.2f Y=%.2f' % (h[0], h[1], h[2]), dict(side='Top', x=h[1], y=h[2])))
    return ok, out
