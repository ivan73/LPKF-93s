"""Erzeugung der LMD-Fräsdaten aus Gerber/Excellon (gemeinsam für Kommandozeile und Weboberfläche)."""
import json, math, os
from dataclasses import dataclass, field, asdict
from shapely.geometry import Point, Polygon, MultiPolygon, LineString, MultiLineString, GeometryCollection
from shapely.ops import unary_union, linemerge, nearest_points, substring
from .compat import Index

from . import gerber, lmdio

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, 'template.json')
M = 1e-3  # mm -> m


@dataclass
class Options:
    iso_tool: str = 'Universal Cutter 0.2 mm (8 mil)'
    iso_width: float = 0.0     # gewünschte Isolationsbreite in mm; 0 = 'passes' Kanäle verwenden
    passes: int = 1            # nur wenn iso_width == 0
    pad_width: float = 0.0     # Pad-Freistellung: um Lötpads so breit freifräsen (mm); 0 = aus
    overlap: float = 0.3       # Mindestüberlappung benachbarter Kanäle (Anteil des Fräserdurchmessers)
    rubout_tool: str = 'End Mill 1.0 mm (39 mil)'      # '' = kein Ausräumen
    rubout_overlap: float = 0.2
    cut_tool: str = 'End Mill 2.0 mm (79 mil) long'
    tabs: int = 0
    tab_width: float = 1.0
    hole_router: str = 'Contour Router 2.0 mm (79 mil)'
    marking: bool = True
    mark_tool: str = 'Universal Cutter 0.2 mm (8 mil)'   # Werkzeug für MarkingDrills (Ankörnen)
    drill_map: dict = field(default_factory=dict)       # {'0.610': 'Spiral Drill 0.8 mm', ...}; fehlt/'auto' = nächster Bohrer
    user: str = 'LBS-Meran'


@dataclass
class Inputs:
    top: str = None          # Gerber-Text Bestückungsseite
    bottom: str = None       # Gerber-Text Lötseite
    outline: str = None      # Gerber-Text Umriss
    drills: list = field(default_factory=list)   # [(excellon_text, plated: bool), ...]
    mask_top: str = None     # Lötstoppmaske Top (nur für Pad-Freistellung)
    mask_bottom: str = None  # Lötstoppmaske Bottom


@dataclass
class Result:
    lmd: bytes
    log: list
    warnings: list
    board: object
    stats: dict


def load_template(path=TEMPLATE):
    tpl = json.load(open(path))
    return [dict(phase=p['phase'], layer=p['layer'], tools=[tuple(t) for t in p['tools']], cmds=[]) for p in tpl]


def tool_diameters(path=TEMPLATE):
    """{Werkzeugname: Durchmesser in mm} – für die Live-Anzeige der Kanalplanung."""
    return {t[3]: round(t[2] * 1e3, 4) for p in load_template(path) for t in p['tools']}


def tool_choices(path=TEMPLATE):
    """Werkzeugnamen je Phase – für Auswahllisten."""
    out = {}
    for p in load_template(path):
        out.setdefault(p['phase'], [t[3] for t in p['tools']])
    return out


# ---------------------------------------------------------------- Geometrie-Helfer
def polys(g):
    if g is None or g.is_empty: return []
    if isinstance(g, Polygon): return [g]
    if isinstance(g, (MultiPolygon, GeometryCollection)):
        return [p for x in g.geoms for p in polys(x)]
    return []


def lines(g):
    if g is None or g.is_empty: return []
    if isinstance(g, LineString): return [list(g.coords)]
    if isinstance(g, Polygon): return lines(g.exterior) + [c for r in g.interiors for c in lines(r)]
    if hasattr(g, 'geoms'): return [c for x in g.geoms for c in lines(x)]
    return []


def rings(g):
    out = []
    for p in polys(g):
        out.append(list(p.exterior.coords))
        out += [list(r.coords) for r in p.interiors]
    return out


def order_paths(paths, start=(0.0, 0.0)):
    """Greedy-Nearest-Neighbour; geschlossene Ringe beginnen am nächstgelegenen Punkt."""
    rest = [p for p in paths if len(p) >= 2]
    out = []; cur = start
    while rest:
        best = None
        for idx, p in enumerate(rest):
            if p[0] == p[-1]:
                k = min(range(len(p) - 1), key=lambda j: (p[j][0] - cur[0]) ** 2 + (p[j][1] - cur[1]) ** 2)
                cand = ((p[k][0] - cur[0]) ** 2 + (p[k][1] - cur[1]) ** 2, idx, ('ring', k))
            else:
                d0 = (p[0][0] - cur[0]) ** 2 + (p[0][1] - cur[1]) ** 2
                d1 = (p[-1][0] - cur[0]) ** 2 + (p[-1][1] - cur[1]) ** 2
                cand = (min(d0, d1), idx, ('rev',) if d1 < d0 else ('fwd',))
            if best is None or cand[0] < best[0]: best = cand
        _, idx, how = best
        p = rest.pop(idx)
        if how[0] == 'ring':
            k = how[1]; p = p[k:-1] + p[:k] + [p[k]]
        elif how[0] == 'rev':
            p = p[::-1]
        out.append(p); cur = p[-1]
    return improve_order(out, start)


def link_paths(paths, copper, r, max_gap=1.0):
    """Aufeinanderfolgende Bahnen ohne Abheben verbinden, wenn die Verbindungsstrecke kurz ist
    und überall mindestens Werkzeugradius (+2 µm) Abstand zum Kupfer hält. Spart Absenkungen."""
    if not paths:
        return paths
    try:
        from shapely.prepared import prep
        danger = prep(copper.buffer(r + 0.002, 16))
        hits = danger.intersects
    except Exception:
        danger = copper.buffer(r + 0.002, 16)
        hits = danger.intersects
    out = [list(paths[0])]
    for p in paths[1:]:
        a, b = out[-1][-1], p[0]
        d = math.dist(a, b)
        if d < 1e-9:
            out[-1].extend(p[1:])
        elif d <= max_gap and not hits(LineString([a, b])):
            out[-1].extend(p)
        else:
            out.append(list(p))
    return out


def _rot_ring(p, k):
    """Ring so drehen, dass er am Punkt k beginnt (und endet)."""
    return p[k:-1] + p[:k] + [p[k]]


def improve_order(paths, start=(0.0, 0.0), budget=2.0):
    """Leerwege nachoptimieren: 2-opt (Teilfolgen umkehren, offene Bahnen werden dabei gewendet)
    im Wechsel mit der Wahl des besten Einstiegspunkts geschlossener Ringe. Zeitbudget in Sekunden."""
    import time
    if len(paths) < 3:
        return paths
    t_end = time.time() + budget
    P = list(paths)
    D = math.dist

    def ends():
        return [p[0] for p in P], [p[-1] for p in P]

    for _round in range(4):
        changed = False
        # --- 2-opt
        N, X = ends()
        n = len(P)
        improved = True
        while improved and time.time() < t_end:
            improved = False
            for i in range(n - 1):
                prevX = X[i - 1] if i > 0 else start
                a = D(prevX, N[i])
                for j in range(i + 1, n):
                    nxt = N[j + 1] if j + 1 < n else None
                    old = a + (D(X[j], nxt) if nxt else 0.0)
                    new = D(prevX, X[j]) + (D(N[i], nxt) if nxt else 0.0)
                    if new < old - 1e-9:
                        P[i:j + 1] = [p[::-1] for p in reversed(P[i:j + 1])]
                        N, X = ends()
                        a = D(prevX, N[i])
                        improved = changed = True
                if time.time() > t_end:
                    break
        # --- Einstiegspunkte der Ringe
        for i, p in enumerate(P):
            if p[0] != p[-1] or len(p) < 4:
                continue
            prevX = P[i - 1][-1] if i > 0 else start
            nxt = P[i + 1][0] if i + 1 < len(P) else None
            cost = lambda v: D(prevX, v) + (D(v, nxt) if nxt else 0.0)
            k = min(range(len(p) - 1), key=lambda m: cost(p[m]))
            if cost(p[k]) < cost(p[0]) - 1e-9:
                P[i] = _rot_ring(p, k); changed = True
        if not changed or time.time() > t_end:
            break
    return P


def emit_paths(cmds, tool, paths):
    if not paths: return
    cmds.append((0x10, tool))
    for p in paths:
        cmds.append((0x0c, (p[0][0] * M, p[0][1] * M)))
        last = p[0]
        for q in p[1:]:
            if q != last:
                cmds.append((0x0d, (q[0] * M, q[1] * M))); last = q


def emit_circle(cmds, cx, cy, r):
    """Kreis wie CircuitCAM: Start Mitte, Linie auf Radius, zwei Halbbögen gegen den Uhrzeigersinn."""
    cmds.append((0x0c, (cx * M, cy * M)))
    cmds.append((0x0d, ((cx - r) * M, cy * M)))
    cmds.append((0x11, ((cx + r) * M, cy * M, cx * M, cy * M, r * M)))
    cmds.append((0x11, ((cx - r) * M, cy * M, cx * M, cy * M, r * M)))


def tool_of(phase, name):
    for t in phase['tools']:
        if t[3] == name: return t[0], t[2] * 1e3
    raise ValueError('Werkzeug "%s" fehlt in Phase %s/%s' % (name, phase['phase'], phase['layer']))


# ---------------------------------------------------------------- Fräsbahnen
def channel_plan(d_tool, width=0.0, overlap=0.3, passes=1):
    """Kanalplanung. Liefert dict(offsets, n, width, step, overlap).
    offsets = Abstand Fräsermitte–Kupferrand je Kanal (mm).
    Mit width > 0: so viele Kanäle, dass die Breite genau erreicht wird und benachbarte Kanäle
    sich mindestens um 'overlap' überlappen; die Kanäle werden gleichmäßig verteilt."""
    r = d_tool / 2
    overlap = min(max(overlap, 0.0), 0.95)
    max_step = d_tool * (1 - overlap)
    if width and width > d_tool + 1e-6:
        n = 1 + math.ceil((width - d_tool) / max_step - 1e-9)
        step = (width - d_tool) / (n - 1)
    else:
        n = max(1, int(passes)) if not width else 1
        step = max_step
    offsets = [r + k * step for k in range(n)]
    real_w = d_tool + (n - 1) * step
    return dict(offsets=offsets, n=n, width=real_w, step=step if n > 1 else 0.0,
                overlap=(1 - step / d_tool) if n > 1 else None)


def isolation(copper, board, d_tool, offsets, d_rub, rub_overlap):
    """(Isolationsbahnen, Ausräumbahnen) in mm.
    Ringe werden je Kupferinsel gebildet und nur dort gekappt, wo sie fremdes Kupfer
    anfressen würden – so bleibt auch bei Abstand == Fräserdurchmesser die Trennbahn erhalten."""
    r = d_tool / 2
    TOL = 0.003
    isl = polys(copper)
    tree = Index(isl)
    iso = []
    for off in offsets:
        bufs = [p.buffer(off, 24) for p in isl]
        for i in range(len(isl)):
            g = MultiLineString(rings(bufs[i])).intersection(board)
            nb = [j for j in tree.query(bufs[i].buffer(off)) if j != i]
            if nb:
                g = g.difference(unary_union([isl[j].buffer(off - TOL, 24) for j in nb]))
            iso += lines(g)
    if iso:
        iso = lines(linemerge([LineString(p) for p in iso if len(p) >= 2]))
    rub = []
    if d_rub:
        R = d_rub / 2
        iso_w = offsets[-1] + r
        # Mittelpunktsbereich des Ausräumfräsers: Schneide endet am Außenrand des Isolationskanals,
        # über den Platinenrand darf er hinaus (dort wird ohnehin weggefräst)
        area = board.buffer(R * 0.9, 16).difference(copper.buffer(iso_w + R, 24))
        area = area.buffer(-0.01).buffer(0.01)
        stepR = d_rub * (1 - rub_overlap)
        n = 0
        while not area.is_empty and n < 200:
            rub += rings(area)
            area = area.buffer(-stepR, 16)
            n += 1
    simp = lambda ps: [list(LineString(p).simplify(0.001).coords) for p in ps if len(p) >= 2]
    return simp(iso), simp(rub)


def pad_regions(copper, mask_txt=None, holes=()):
    """Kupfer der Lötpads. Mit Lötstoppmaske: Kupfer innerhalb der Maskenöffnungen (THT + SMD).
    Ohne Maske: Kupfer um die Bohrungen (nur bedrahtete Bauteile). Liefert (Geometrie, Quelle)."""
    if mask_txt:
        mask, _ = gerber.read_gerber(mask_txt)
        if not mask.is_empty:
            return copper.intersection(mask), 'Lötstoppmaske'
    if holes:
        rings = unary_union([Point(h[1], h[2]).buffer(h[0] / 2 + 0.9, 16) for h in holes])
        return copper.intersection(rings), 'Bohrungen (nur THT)'
    return Polygon(), None


def pad_clearance(copper, pads, board, d_tool, offsets):
    """Zusätzliche Ringe um die Pads in den Abständen 'offsets' (Fräsermitte–Padrand).
    Gekappt überall, wo sie irgendein Kupfer (auch Leiterbahnen am Pad) anfressen würden."""
    if pads.is_empty or not offsets:
        return []
    r = d_tool / 2
    keep_out = copper.buffer(r + 0.002, 24)   # nie ins Kupfer, auch nicht um µm
    out = []
    for off in offsets:
        g = MultiLineString(rings(pads.buffer(off, 24))).intersection(board).difference(keep_out)
        out += lines(g)
    if out:
        out = lines(linemerge([LineString(p) for p in out if len(p) >= 2]))
    return [list(LineString(p).simplify(0.001).coords) for p in out if len(p) >= 2]


def clearance_warnings(copper, d_tool, label):
    ps = polys(copper)
    if not ps: return []
    tree = Index(ps); out = []
    for i, p in enumerate(ps):
        for j in tree.query(p.buffer(d_tool)):
            if j <= i: continue
            dist = p.distance(ps[j])
            if dist < d_tool - 1e-4:
                a, b = nearest_points(p, ps[j])
                out.append((dist, (a.x + b.x) / 2, (a.y + b.y) / 2))
    return ['%s: Abstand %.3f mm < Fräser %.2f mm bei X=%.2f Y=%.2f – dort wird NICHT getrennt!'
            % (label, d, d_tool, x, y) for d, x, y in sorted(out)]


def dkey(d):
    """Schlüssel für einen Bohrdurchmesser aus der Excellon-Datei (mm, 3 Nachkommastellen)."""
    return '%.3f' % d


def drill_tool(ph, d, max_drill, drill_map=None):
    """Bohrer für Durchmesser d: Vorgabe aus drill_map, sonst nächstgelegener Spiralbohrer
    (bei Gleichstand der größere); None = fräsen."""
    want = (drill_map or {}).get(dkey(d))
    if want and want != 'auto':
        for nr, typ, dia, name in ph['tools']:
            if name == want:
                return nr
    if d > max_drill + 0.05:
        return None
    best = None
    for nr, typ, dia, name in ph['tools']:
        if typ != 1: continue
        dm = dia * 1e3
        key = (round(abs(dm - d), 4), -dm)
        if best is None or key < best[0]: best = (key, nr)
    return best[1]


def tab_paths(path, n, gap):
    """Kontur in Abschnitte teilen, Stege mittig auf den n längsten geraden Kanten."""
    c = list(path.coords); pos = 0.0; edges = []
    for p0, p1 in zip(c, c[1:]):
        l = math.dist(p0, p1); edges.append((l, pos + l / 2)); pos += l
    edges.sort(reverse=True)
    centers = sorted(m for l, m in edges[:n] if l > gap + 2)
    if not centers:
        return [c], 0
    L = path.length; cuts = [(m - gap / 2, m + gap / 2) for m in centers]
    segs = []
    for k, (_, e0) in enumerate(cuts):
        s = e0; e = cuts[(k + 1) % len(cuts)][0]
        if e <= s: e += L
        if e <= L:
            segs.append(list(substring(path, s, e).coords))
        else:
            segs.append(list(substring(path, s, L).coords) + list(substring(path, 0, e - L).coords)[1:])
    return segs, len(centers)


# ---------------------------------------------------------------- Hauptfunktion
def generate(inp: Inputs, opt: Options = None, template=TEMPLATE) -> Result:
    opt = opt or Options()
    log, warn = [], []
    stats = {}
    phases = load_template(template)
    P = {(p['phase'], p['layer']): p for p in phases}

    if not inp.outline:
        raise ValueError('Kein Platinenumriss (Outline/Border/Profile) angegeben')
    og, l0 = gerber.read_gerber(inp.outline)
    board, _ = gerber.outline_polygon(l0, og if not og.is_empty else None)
    bx0, by0, bx1, by1 = board.bounds
    log.append('Platine: %.2f x %.2f mm (X %.2f..%.2f, Y %.2f..%.2f)' % (bx1 - bx0, by1 - by0, bx0, bx1, by0, by1))
    stats['size'] = (round(bx1 - bx0, 2), round(by1 - by0, 2))
    corner = (bx0, by0)   # Startpunkt der Wegoptimierung: linke untere Platinenecke

    holes_all = [h for txt, _ in inp.drills for h in gerber.read_excellon(txt)]

    # --- Isolation
    for side, txt, key, mask in (('Top', inp.top, ('MillingTop', 'InsulateTop'), inp.mask_top),
                                 ('Bottom', inp.bottom, ('MillingBottom', 'InsulateBottom'), inp.mask_bottom)):
        if not txt: continue
        ph = P[key]
        copper, _ = gerber.read_gerber(txt)
        t_iso, d_iso = tool_of(ph, opt.iso_tool)
        t_rub, d_rub = (tool_of(ph, opt.rubout_tool) if opt.rubout_tool else (None, 0))
        plan = channel_plan(d_iso, opt.iso_width, opt.overlap, opt.passes)
        if opt.iso_width and opt.iso_width < d_iso - 1e-6:
            w = 'Isolationsbreite %.2f mm ist kleiner als der Fräser (Ø%.2f mm) – es wird Fräserbreite verwendet' % (opt.iso_width, d_iso)
            if w not in warn: warn.append(w); log.append('  Hinweis: ' + w)
        log.append('%s: %d Kupferinseln, Isolation %.2f mm breit = %d %s mit Ø%.2f mm%s' % (
            side, len(polys(copper)), plan['width'], plan['n'], 'Kanal' if plan['n'] == 1 else 'Kanäle', d_iso,
            (', Versatz %.3f mm, Überlappung %.0f %%' % (plan['step'], plan['overlap'] * 100)) if plan['n'] > 1 else ''))
        w = clearance_warnings(copper, d_iso, side)
        warn += w; log += ['  WARNUNG ' + x for x in w]
        iso, rub = isolation(copper, board, d_iso, plan['offsets'], d_rub, opt.rubout_overlap)
        if opt.pad_width and opt.pad_width > plan['width'] + 1e-6:
            pads, src = pad_regions(copper, mask, holes_all)
            pplan = channel_plan(d_iso, opt.pad_width, opt.overlap)
            extra = [o for o in pplan['offsets'] if o > plan['offsets'][-1] + 1e-6]
            pr = pad_clearance(copper, pads, board, d_iso, extra)
            iso += pr
            log.append('  Pad-Freistellung %.2f mm um %d Pads (erkannt über %s): +%d Ringe, %.0f mm' % (
                pplan['width'], len(polys(pads)), src or '–', len(extra), sum(LineString(p).length for p in pr)))
            if src is None:
                w = '%s: Pad-Freistellung nicht möglich – weder Lötstoppmaske noch Bohrungen vorhanden' % side
                warn.append(w); log.append('  Hinweis: ' + w)
        iso = order_paths(iso, corner); rub = order_paths(rub, iso[-1][-1] if iso else corner)
        n_before = len(iso) + len(rub)
        iso = link_paths(iso, copper, d_iso / 2)
        if d_rub: rub = link_paths(rub, copper, d_rub / 2)
        log.append('  Absenkungen: %d (ohne Verbinden wären es %d)' % (len(iso) + len(rub), n_before))
        emit_paths(ph['cmds'], t_iso, iso)
        if t_rub: emit_paths(ph['cmds'], t_rub, rub)
        L = lambda ps: sum(LineString(p).length for p in ps)
        log.append('  Isolation: %d Bahnen (%.0f mm), Ausräumen: %d Bahnen (%.0f mm)' % (len(iso), L(iso), len(rub), L(rub)))
        stats[side] = dict(channels=plan['n'], width=round(plan['width'], 3), islands=len(polys(copper)), iso_mm=round(L(iso)), rub_mm=round(L(rub)))

    # --- Bohren
    all_holes = []
    for txt, plated in inp.drills:
        holes = gerber.read_excellon(txt)
        all_holes += holes
        key = ('DrillingPlated', 'DrillPlated') if plated else ('DrillingUnplated', 'DrillUnplated')
        ph = P[key]
        max_drill = max(t[2] for t in ph['tools'] if t[1] == 1) * 1e3
        t_router, d_router = tool_of(ph, opt.hole_router)
        groups, milled = {}, []
        for h in holes:
            if h[3] == 'slot':
                w = 'Langloch bei X=%.2f Y=%.2f wird nur als Bohrung am Startpunkt behandelt' % (h[1], h[2])
                warn.append(w); log.append('  WARNUNG ' + w)
            t = drill_tool(ph, h[0], max_drill, opt.drill_map)
            if t is None: milled.append(h)
            else: groups.setdefault(t, []).append((h[1], h[2]))
        for t in sorted(groups):
            ph['cmds'].append((0x10, t))
            for p in order_paths([[p, p] for p in groups[t]], corner):
                ph['cmds'].append((0x0f, (p[0][0] * M, p[0][1] * M)))
        if milled:
            ph['cmds'].append((0x10, t_router))
            for d, x, y, *_ in milled:
                r = (d - d_router) / 2
                if r < 0.05:
                    w = 'Bohrung Ø%.2f bei X=%.2f Y=%.2f zu klein für %s' % (d, x, y, opt.hole_router)
                    warn.append(w); log.append('  WARNUNG ' + w); continue
                emit_circle(ph['cmds'], x, y, r)
        names = {t[0]: t[3] for t in ph['tools']}
        log.append('Bohrungen %s: %d' % ('durchkontaktiert' if plated else 'nicht durchkontaktiert', len(holes)))
        for t in sorted(groups):
            log.append('  %-30s %3d' % (names[t], len(groups[t])))
        if milled:
            log.append('  %-30s %3d (gefräst: %s)' % (names[t_router], len(milled), ', '.join('Ø%.2f' % h[0] for h in milled)))
        if opt.marking:
            mk = P[('MarkingDrills', key[1])]
            if opt.mark_tool and opt.mark_tool != mk['tools'][0][3]:
                dia = tool_diameters().get(opt.mark_tool, 0.2)
                mk['tools'] = [(1, 1, dia / 1e3, opt.mark_tool)]
            mk['cmds'].append((0x10, mk['tools'][0][0]))
            for p in order_paths([[(h[1], h[2])] * 2 for h in holes], corner):
                mk['cmds'].append((0x0f, (p[0][0] * M, p[0][1] * M)))
    stats['holes'] = len(all_holes)

    # --- Außenkontur
    ph = P[('CuttingOutside', 'CuttingOutside')]
    t_cut, d_cut = tool_of(ph, opt.cut_tool)
    ext = board.buffer(d_cut / 2, 24).exterior
    # Umlaufrichtung wie CircuitCAM: gegen den Uhrzeigersinn
    path = LineString(ext.coords if ext.is_ccw else list(ext.coords)[::-1])
    if opt.tabs > 0:
        cpaths, n = tab_paths(path, opt.tabs, opt.tab_width + d_cut)
        if n < opt.tabs:
            w = 'nur %d gerade Kanten lang genug für Haltestege' % n
            warn.append(w); log.append('  Hinweis: ' + w)
    else:
        cpaths, n = [list(path.coords)], 0
    emit_paths(ph['cmds'], t_cut, [list(LineString(p).simplify(0.002).coords) for p in cpaths])
    log.append('Außenkontur: %s, %d Haltesteg(e)' % (opt.cut_tool, n))

    data = lmdio.write(lmdio.make_header(opt.user), phases)
    log.append('LMD: %d Bytes' % len(data))
    return Result(data, log, warn, board, stats)


def drill_summary(drills, top=None, bottom=None, template=TEMPLATE):
    """Übersicht je Bohrdurchmesser für die Auswahl in der Weboberfläche.
    drills: [(excellon_text, plated)]. Liefert Liste von dict(key, d, count, plated, via, auto, pad_r)
    pad_r = kleinster Abstand Lochmitte–Kupferrand (Top/Bottom) in mm -> Restring = pad_r - Bohrer/2."""
    phases = load_template(template)
    P = {(p['phase'], p['layer']): p for p in phases}
    cus = [core_cu for core_cu in (gerber.read_gerber(t)[0] if t else None for t in (top, bottom)) if core_cu is not None]
    cu_polys = [polys(c) for c in cus]
    groups = {}
    for txt, plated in drills:
        funcs = gerber.excellon_functions(txt)
        for h in gerber.read_excellon(txt):
            k = (dkey(h[0]), plated)
            g = groups.setdefault(k, dict(key=dkey(h[0]), d=h[0], count=0, plated=plated, via=None, pad_r=None))
            g['count'] += 1
            f = funcs.get(dkey(h[0]))
            if f: g['via'] = (f == 'via')
            pt = Point(h[1], h[2])
            for ps in cu_polys:
                for p in ps:
                    if p.contains(pt):
                        r = p.boundary.distance(pt)
                        g['pad_r'] = r if g['pad_r'] is None else min(g['pad_r'], r)
                        break
    out = []
    for (key, plated), g in sorted(groups.items(), key=lambda kv: (not kv[0][1], kv[1]['d'])):
        ph = P[('DrillingPlated', 'DrillPlated') if plated else ('DrillingUnplated', 'DrillUnplated')]
        max_drill = max(t[2] for t in ph['tools'] if t[1] == 1) * 1e3
        t = drill_tool(ph, g['d'], max_drill)
        g['auto'] = next((x[3] for x in ph['tools'] if x[0] == t), None) if t else 'gefräst'
        if g['via'] is None:
            g['via_hint'] = g['d'] <= 0.7          # Faustregel: so kleine Löcher sind meist Vias
        else:
            g['via_hint'] = g['via']
        g['pad_r'] = None if g['pad_r'] is None else round(g['pad_r'], 3)
        out.append(g)
    drills_avail = [(t[3], round(t[2] * 1e3, 3)) for t in P[('DrillingPlated', 'DrillPlated')]['tools'] if t[1] == 1]
    return dict(groups=out, drills=drills_avail)
