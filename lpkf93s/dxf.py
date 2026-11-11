"""DXF-Import (AutoCAD & Co.) für Frontplatten, Gehäusedeckel und ähnliche Fräs-/Bohrarbeiten.

Je Layer wird festgelegt, was mit der Geometrie passiert:
  auto      – automatisch: kleine Kreise bohren, größte geschlossene Kontur = Außenkontur,
              geschlossene Konturen darin = Ausschnitte, offene Linien und Texte gravieren
  outer     – Außenkontur: Fräser außen an der Linie (Radiuskorrektur nach außen), Haltestege möglich
  cutout    – Ausschnitt: Fräser innen an der Linie (Radiuskorrektur nach innen)
  engrave   – Gravur: Fräsermitte genau auf der Linie
  drill     – Kreise bohren (größer als der größte Bohrer: als Ausschnitt fräsen)
  ignore    – nicht verwenden

DWG kann nicht gelesen werden (proprietär) – in AutoCAD „Speichern unter → DXF“.
"""
import io, math, os
from dataclasses import dataclass, field

from shapely.geometry import Polygon, LineString, Point, MultiLineString
from shapely.ops import unary_union, linemerge

from . import core, lmdio

OPS = {
    'auto': 'automatisch',
    'outer': 'Außenkontur',
    'cutout': 'Ausschnitt',
    'engrave': 'Gravieren',
    'drill': 'Bohren',
    'ignore': 'nicht verwenden',
}
UNITS = {0: 1.0, 1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0, 8: 0.0254, 9: 0.0254 * 1000 / 1000, 10: 914.4, 13: 0.001, 14: 100.0}
FLAT = 0.005   # mm – Sehnenfehler beim Annähern von Bögen und Splines


def _ezdxf():
    try:
        import ezdxf  # noqa
        return ezdxf
    except ImportError:
        raise ValueError('Für DXF-Dateien fehlt das Python-Paket ezdxf (Raspberry Pi: sudo apt install python3-ezdxf)')


@dataclass
class Layer:
    name: str
    closed: list = field(default_factory=list)    # [[(x,y)…]] geschlossene Linienzüge
    open: list = field(default_factory=list)      # [[(x,y)…]] offene Linienzüge
    circles: list = field(default_factory=list)   # [(cx, cy, d)]
    text: list = field(default_factory=list)      # [[(x,y)…]] Textumrisse
    skipped: int = 0


UNIT_CHOICES = {'auto': 'aus der Datei', 'mm': 'mm', 'inch': 'Zoll', 'cm': 'cm'}


def read(path_or_bytes, unit='auto'):
    """DXF lesen -> (layers {name: Layer}, info dict). Blöcke werden aufgelöst, Einheiten nach mm umgerechnet.
    unit: 'auto' ($INSUNITS der Datei, fehlt sie: mm) oder fest 'mm' / 'inch' / 'cm'."""
    ezdxf = _ezdxf()
    import logging
    logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)
    from ezdxf import recover, path as ezpath
    if isinstance(path_or_bytes, (bytes, bytearray)):
        doc, auditor = recover.read(io.BytesIO(path_or_bytes))
    else:
        doc, auditor = recover.readfile(path_or_bytes)
    units = doc.header.get('$INSUNITS', 0)
    k = {'mm': 1.0, 'inch': 25.4, 'cm': 10.0}.get(unit) or UNITS.get(units, 1.0)
    layers = {}
    text2path = None
    info = dict(units=units, unit=unit, factor=k, version=doc.dxfversion, text_failed=0)

    def L(name):
        if name not in layers:
            layers[name] = Layer(name)
        return layers[name]

    def pts_of(sub):
        return [(v.x * k, v.y * k) for v in sub.flattening(FLAT / max(k, 1e-9))]

    def flat(entities, parent=None, depth=0):
        # Blöcke auflösen; Elemente auf Layer 0 im Block erben (wie in AutoCAD) den Layer der Blockreferenz
        for e in entities:
            lay = e.dxf.get('layer', '0')
            if parent and lay == '0':
                lay = parent
            if e.dxftype() == 'INSERT' and depth < 16:
                try:
                    yield from flat(e.virtual_entities(), lay, depth + 1)
                except Exception:
                    yield e, lay
            else:
                yield e, lay

    for e, layname in flat(doc.modelspace()):
        t = e.dxftype()
        lay = L(layname)
        try:
            if t == 'CIRCLE':
                c = e.ocs().to_wcs(e.dxf.center)
                lay.circles.append((c.x * k, c.y * k, 2 * e.dxf.radius * k))
            elif t in ('TEXT', 'MTEXT', 'ATTRIB'):
                if text2path is None:
                    from ezdxf.addons import text2path as _t2p
                    text2path = _t2p
                for p in text2path.make_paths_from_entity(e):
                    for sub in p.sub_paths():
                        pts = pts_of(sub)
                        if len(pts) >= 2:
                            lay.text.append(pts)
            elif t in ('LINE', 'ARC', 'LWPOLYLINE', 'POLYLINE', 'ELLIPSE', 'SPLINE'):
                p = ezpath.make_path(e)
                for sub in p.sub_paths():
                    pts = pts_of(sub)
                    if len(pts) < 2:
                        continue
                    if sub.is_closed or math.dist(pts[0], pts[-1]) < 1e-3:
                        if math.dist(pts[0], pts[-1]) >= 1e-9:
                            pts.append(pts[0])
                        lay.closed.append(pts)
                    else:
                        lay.open.append(pts)
            else:
                lay.skipped += 1
        except Exception:
            if t in ('TEXT', 'MTEXT', 'ATTRIB'):
                info['text_failed'] += 1
            lay.skipped += 1
    # offene Stücke zu Linienzügen verbinden (z.B. Rechteck aus vier LINEs); geschlossene erkennen
    for lay in layers.values():
        if lay.open:
            snap = lambda q: (round(q[0], 4), round(q[1], 4))
            merged = linemerge([LineString([snap(q) for q in p]) for p in lay.open if len(p) >= 2])
            lay.open = []
            for ls in getattr(merged, 'geoms', [merged]):
                c = list(ls.coords)
                if len(c) >= 4 and math.dist(c[0], c[-1]) < 1e-3:
                    c[-1] = c[0]
                    lay.closed.append(c)
                else:
                    lay.open.append(c)
    layers = {n: l for n, l in layers.items() if l.closed or l.open or l.circles or l.text}
    xs, ys = [], []
    for l in layers.values():
        for p in l.closed + l.open + l.text:
            xs += [q[0] for q in p]; ys += [q[1] for q in p]
        for c in l.circles:
            xs += [c[0] - c[2] / 2, c[0] + c[2] / 2]; ys += [c[1] - c[2] / 2, c[1] + c[2] / 2]
    info['size'] = (round(max(xs) - min(xs), 2), round(max(ys) - min(ys), 2)) if xs else (0, 0)
    return layers, info


def summary(path_or_bytes, max_drill=2.4, unit='auto'):
    """Übersicht für die Weboberfläche: je Layer Anzahl Konturen, Linien, Kreise (davon bohrbar), Texte."""
    layers, info = read(path_or_bytes, unit)
    out = []
    for n in sorted(layers):
        l = layers[n]
        out.append(dict(name=n, closed=len(l.closed), open=len(l.open), circles=len(l.circles),
                        drillable=sum(1 for c in l.circles if c[2] <= max_drill + 0.05), text=len(l.text) > 0,
                        skipped=l.skipped))
    return dict(layers=out, info=info)


def _poly(pts):
    pg = Polygon(pts)
    return pg if pg.is_valid else pg.buffer(0)


def plan(layers, layer_ops, max_drill=2.4):
    """Geometrie je Bearbeitung einsortieren. Liefert dict(outer, cutout, engrave, drill) und Hinweise."""
    jobs = dict(outer=[], cutout=[], engrave=[], drill=[])
    notes = []
    autos = []
    for n, l in layers.items():
        op = layer_ops.get(n, 'auto')
        if op == 'ignore':
            continue
        if op == 'auto':
            autos.append(l)
            continue
        if op == 'drill':
            jobs['drill'] += [c for c in l.circles if c[2] <= max_drill + 0.05]
            jobs['cutout'] += [Point(c[0], c[1]).buffer(c[2] / 2, 64) for c in l.circles if c[2] > max_drill + 0.05]
            if l.closed or l.open or l.text:
                notes.append(('hinweis', 'Layer „%s“ ist auf Bohren gestellt – nur Kreise werden gebohrt, andere Geometrie ignoriert' % n, None))
            continue
        if op == 'engrave':
            jobs['engrave'] += l.closed + l.open + l.text
            jobs['engrave'] += [_circle_pts(*c) for c in l.circles]
            continue
        # outer / cutout
        polys = [_poly(p) for p in l.closed] + [Point(c[0], c[1]).buffer(c[2] / 2, 64) for c in l.circles]
        jobs[op] += [p for p in polys if not p.is_empty]
        for p in l.open:
            q = p[len(p) // 2]
            notes.append(('fehler', 'Layer „%s“: offene Linie (nicht geschlossen) kann nicht als %s gefräst werden – wird graviert (bei X=%.2f Y=%.2f)'
                          % (n, OPS[op], q[0], q[1]), (q[0], q[1])))
            jobs['engrave'].append(p)
        jobs['engrave'] += l.text
    if autos:
        closed = [_poly(p) for l in autos for p in l.closed]
        big_circles = [Point(c[0], c[1]).buffer(c[2] / 2, 64) for l in autos for c in l.circles if c[2] > max_drill + 0.05]
        jobs['drill'] += [c for l in autos for c in l.circles if c[2] <= max_drill + 0.05]
        jobs['engrave'] += [p for l in autos for p in l.open + l.text]
        closed = [p for p in closed if not p.is_empty]
        if closed:
            closed.sort(key=lambda p: -p.area)
            outer = closed[0]
            shell = Polygon(outer.exterior)
            jobs['outer'].append(outer)
            for p in closed[1:]:
                (jobs['cutout'] if shell.contains(p.representative_point()) else jobs['outer']).append(p)
        jobs['cutout'] += big_circles
    return jobs, notes


def _circle_pts(cx, cy, d, n=96):
    r = d / 2
    pts = [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]
    return pts + [pts[0]]


def generate(path_or_bytes, opt: core.Options = None, layer_ops=None, template=core.TEMPLATE, progress=None, unit='auto'):
    """LMD aus DXF erzeugen. Liefert core.Result und Prüfzeilen [(gut, text, ort)]."""
    opt = opt or core.Options()
    prog = progress or (lambda f, t: None)
    layer_ops = layer_ops or {}
    log, warn, marks, checks = [], [], [], []
    prog(0.05, 'DXF lesen')
    layers, info = read(path_or_bytes, unit)
    if not layers:
        raise ValueError('Die DXF-Datei enthält keine verwertbare Geometrie (Linien, Bögen, Polylinien, Kreise, Texte)')
    log.append('DXF %s, Einheit %s (Faktor %g → mm), %d Layer' % (info['version'], {'auto': info['units']}.get(unit, unit), info['factor'], len(layers)))
    if info['text_failed']:
        warn.append('%d Texte konnten nicht in Umrisse umgewandelt werden – in AutoCAD vorher „EXPLODE“/„TXTEXP“ anwenden' % info['text_failed'])

    phases = core.load_template(template)
    P = {(p['phase'], p['layer']): p for p in phases}
    ph_drill = P[('DrillingUnplated', 'DrillUnplated')]
    max_drill = max(t[2] for t in ph_drill['tools'] if t[1] == 1) * 1e3
    jobs, notes = plan(layers, layer_ops, max_drill)

    # Nullpunkt: linke untere Ecke aller Geometrie
    xs, ys = [], []
    for g in jobs['outer'] + jobs['cutout']:
        b = g.bounds; xs += [b[0], b[2]]; ys += [b[1], b[3]]
    for p in jobs['engrave']:
        xs += [q[0] for q in p]; ys += [q[1] for q in p]
    for c in jobs['drill']:
        xs += [c[0] - c[2] / 2, c[0] + c[2] / 2]; ys += [c[1] - c[2] / 2, c[1] + c[2] / 2]
    if not xs:
        raise ValueError('Nach der Layer-Zuordnung bleibt nichts zu fräsen')
    dx, dy = -min(xs), -min(ys)
    from shapely import affinity
    jobs['outer'] = [affinity.translate(g, dx, dy) for g in jobs['outer']]
    jobs['cutout'] = [affinity.translate(g, dx, dy) for g in jobs['cutout']]
    jobs['engrave'] = [[(q[0] + dx, q[1] + dy) for q in p] for p in jobs['engrave']]
    jobs['drill'] = [(c[0] + dx, c[1] + dy, c[2]) for c in jobs['drill']]
    notes = [(lv, t, (loc[0] + dx, loc[1] + dy) if loc else None) for lv, t, loc in notes]
    log.append('Nullpunkt: linke untere Ecke der Zeichnung (verschoben um X %+.2f, Y %+.2f mm)' % (dx, dy))
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    log.append('Größe: %.2f x %.2f mm' % (w, h))
    for lv, t, loc in notes:
        warn.append(t); checks.append((lv != 'fehler', t, dict(side='Top', x=round(loc[0], 3), y=round(loc[1], 3)) if loc else None))
        if loc: marks.append(dict(side='Top', x=round(loc[0], 3), y=round(loc[1], 3), text=t, kind='dxf'))
    corner = (0.0, 0.0)

    def problem(text, x, y):
        warn.append(text); log.append('  WARNUNG ' + text)
        loc = dict(side='Top', x=round(x, 3), y=round(y, 3))
        checks.append((False, text, loc)); marks.append(dict(loc, text=text, kind='dxf'))

    # --- Bohren
    prog(0.2, 'Bohrungen')
    if jobs['drill']:
        groups = {}
        for cx, cy, d in jobs['drill']:
            t = core.drill_tool(ph_drill, d, max_drill, opt.drill_map)
            groups.setdefault(t, []).append((cx, cy))
        names = {t[0]: t[3] for t in ph_drill['tools']}
        for t in sorted(groups):
            ph_drill['cmds'].append((0x10, t))
            for p in core.order_paths([[q, q] for q in groups[t]], corner):
                ph_drill['cmds'].append((0x0f, (p[0][0] * core.M, p[0][1] * core.M)))
            log.append('  %-30s %3d' % (names[t], len(groups[t])))
        if opt.marking:
            mk = P[('MarkingDrills', 'DrillUnplated')]
            if opt.mark_tool and opt.mark_tool != mk['tools'][0][3]:
                mk['tools'] = [(1, 1, core.tool_diameters().get(opt.mark_tool, 0.2) / 1e3, opt.mark_tool)]
            mk['cmds'].append((0x10, mk['tools'][0][0]))
            for p in core.order_paths([[(c[0], c[1])] * 2 for c in jobs['drill']], corner):
                mk['cmds'].append((0x0f, (p[0][0] * core.M, p[0][1] * core.M)))
        checks.append((True, 'Bohrungen: %d' % len(jobs['drill']), None))

    # --- Gravieren (Fräsermitte auf der Linie) – Layer TextTop der Fräsphase
    prog(0.4, 'Gravuren')
    if jobs['engrave']:
        ph = P[('MillingTop', 'TextTop')]
        if opt.engrave_tool not in [t[3] for t in ph['tools']]:
            d_eng = core.tool_diameters().get(opt.engrave_tool, 0.2)
            ph['tools'].append((max(t[0] for t in ph['tools']) + 1, 0, d_eng / 1e3, opt.engrave_tool))
        t_eng, _ = core.tool_of(ph, opt.engrave_tool)
        paths = core.order_paths([p for p in jobs['engrave'] if len(p) >= 2], corner)
        core.emit_paths(ph['cmds'], t_eng, paths)
        L = sum(LineString(p).length for p in paths)
        log.append('Gravur: %d Linienzüge, %.0f mm mit %s' % (len(paths), L, opt.engrave_tool))
        checks.append((True, 'Gravur: %d Linienzüge mit %s' % (len(paths), opt.engrave_tool), None))

    # --- Ausschnitte (Fräser innen)
    prog(0.6, 'Ausschnitte')
    material = None
    if jobs['cutout']:
        ph = P[('CuttingInside', 'CuttingInside')]
        t_in, d_in = core.tool_of(ph, opt.cut_in_tool)
        r = d_in / 2
        paths = []; ok_n = 0
        for g in jobs['cutout']:
            inner = g.buffer(-r, 32)
            q = g.representative_point()
            if inner.is_empty:
                # sehr kleiner runder Ausschnitt: in die Mitte eintauchen, wenn er gerade so groß wie der Fräser ist
                if g.area > 0 and abs(math.sqrt(g.area / math.pi) * 2 - d_in) < 0.05:
                    c = g.centroid; paths.append([(c.x, c.y), (c.x, c.y)]); ok_n += 1
                else:
                    problem('Ausschnitt bei X=%.2f Y=%.2f ist zu klein für %s (Ø%.2f mm) – kleineren Fräser wählen oder den Layer auf „Bohren“ stellen'
                            % (q.x, q.y, opt.cut_in_tool, d_in), q.x, q.y)
                continue
            # Ecken innen werden rund (Fräserradius) – Hinweis, wenn die Zeichnung scharfe Innenecken hat
            paths += [list(LineString(p).coords) for p in core.rings(inner)]
            ok_n += 1
        paths = core.order_paths(paths, corner)
        core.emit_paths(ph['cmds'], t_in, paths)
        log.append('Ausschnitte: %d mit %s (Innenecken werden mit r=%.2f mm gerundet)' % (ok_n, opt.cut_in_tool, r))
        checks.append((ok_n == len(jobs['cutout']), 'Ausschnitte: %d von %d fräsbar mit %s' % (ok_n, len(jobs['cutout']), opt.cut_in_tool), None))

    # --- Außenkontur (Fräser außen), Haltestege
    prog(0.8, 'Außenkontur')
    if jobs['outer']:
        ph = P[('CuttingOutside', 'CuttingOutside')]
        t_cut, d_cut = core.tool_of(ph, opt.cut_tool)
        cps = []; nt = 0
        for g in jobs['outer']:
            ext = g.buffer(d_cut / 2, 32).exterior
            path = LineString(ext.coords if ext.is_ccw else list(ext.coords)[::-1])
            if opt.tabs > 0:
                segs, n = core.tab_paths(path, opt.tabs, opt.tab_width + d_cut); nt += n
                cps += segs
            else:
                cps.append(list(path.coords))
        core.emit_paths(ph['cmds'], t_cut, [list(LineString(p).simplify(0.002).coords) for p in cps])
        log.append('Außenkontur: %d mit %s, %d Haltesteg(e)' % (len(jobs['outer']), opt.cut_tool, nt))
        checks.append((True, 'Außenkontur: %d mit %s' % (len(jobs['outer']), opt.cut_tool), None))
        material = unary_union([Polygon(g.exterior) for g in jobs['outer']])
    if material is None:
        from shapely.geometry import box
        material = box(0, 0, w, h)
    if jobs['cutout']:
        material = material.difference(unary_union(jobs['cutout']))

    if jobs['cutout'] and jobs['outer']:
        log.append('Reihenfolge in BoardMaster: Gravur, Bohren, dann zuerst „CuttingInside“ (Ausschnitte), zuletzt „CuttingOutside“')
    data = lmdio.write(lmdio.make_header(opt.user), phases)
    prog(1.0, 'LMD geschrieben')
    board = max(core.polys(material), key=lambda p: p.area) if core.polys(material) else material
    stats = dict(size=(round(w, 2), round(h, 2)), outer=len(jobs['outer']), cutout=len(jobs['cutout']),
                 drill=len(jobs['drill']), engrave=len(jobs['engrave']))
    res = core.Result(data, log, warn, board, stats, marks, {'Top': material})
    return res, checks


def precheck(path_or_bytes, opt=None, layer_ops=None, unit='auto'):
    """Vorprüfung: rechnet die (schnelle) DXF-Erzeugung und liefert die Befunde."""
    res, checks = generate(path_or_bytes, opt, layer_ops, unit=unit)
    items = [dict(level='fehler', text=t, side=(l or {}).get('side'), x=(l or {}).get('x'), y=(l or {}).get('y'))
             for g, t, l in checks if not g]
    return dict(items=items, suggestion=None, board=res.board, copper=res.copper)
