"""Minimaler RS-274X- und Excellon-Leser (ausreichend für EAGLE/Fusion-, KiCad-Standardausgaben).

Gerber -> shapely-Geometrie in mm. Unterstützt:
  %FS, %MO, %AD (C, R, O, P, Makro OC8), %LP (D/C), G01/G02/G03 (G74/G75),
  G36/G37-Regionen, D01/D02/D03, Dnn.
"""
import math, re
from shapely.geometry import Point, Polygon, LineString, MultiPolygon, box
from shapely.ops import unary_union
from shapely import affinity

QS = 16  # Kreissegmente pro Viertelkreis


def _aperture_shape(ap):
    kind, p = ap
    if kind == 'C':
        return Point(0, 0).buffer(p[0] / 2, quad_segs=QS) if p[0] > 0 else None
    if kind == 'R':
        return box(-p[0] / 2, -p[1] / 2, p[0] / 2, p[1] / 2)
    if kind == 'O':
        w, h = p[0], p[1]
        r = min(w, h) / 2
        if w >= h:
            return LineString([(-w / 2 + r, 0), (w / 2 - r, 0)]).buffer(r, quad_segs=QS)
        return LineString([(0, -h / 2 + r), (0, h / 2 - r)]).buffer(r, quad_segs=QS)
    if kind == 'P':
        d, n = p[0], int(p[1]); rot = p[2] if len(p) > 2 else 0
        return Polygon([(d / 2 * math.cos(math.radians(rot + 360 * k / n)),
                         d / 2 * math.sin(math.radians(rot + 360 * k / n))) for k in range(n)])
    if kind == 'OC8':  # EAGLE-Achteck: Außendurchmesser = 1.08239 * Maß, 22.5° gedreht
        d = p[0] * 1.08239
        return Polygon([(d / 2 * math.cos(math.radians(22.5 + 45 * k)),
                         d / 2 * math.sin(math.radians(22.5 + 45 * k))) for k in range(8)])
    raise ValueError('Blende nicht unterstützt: %r' % (ap,))


def _stroke(shape, a, b):
    """Minkowski-Summe einer konvexen Blende entlang einer Strecke."""
    if shape is None:
        return None
    s1 = affinity.translate(shape, *a); s2 = affinity.translate(shape, *b)
    return unary_union([s1, s2]).convex_hull


def read_gerber(text, keep_zero_width=False):
    """Liefert (Geometrie, Liste der Nullbreiten-Linien)."""
    fs = (3, 4); unit = 1.0
    aps = {}; macros = set(); cur_ap = None
    x = y = 0.0; mode = 'G01'; quadrant = 'G75'
    region = False; reg_pts = []; reg_polys = []
    polarity = 'D'; groups = [['D', []]]
    lines0 = []

    def add(g):
        if g is not None and not g.is_empty:
            groups[-1][1].append(g)

    # Erweiterte Befehle (%...%) und Wörter (...*) zerlegen
    tokens = re.findall(r'%[^%]*%|[^*%]+\*', text.replace('\r', '').replace('\n', ''))
    for tok in tokens:
        if tok.startswith('%'):
            body = tok.strip('%')
            for cmd in body.split('*'):
                if not cmd:
                    continue
                if cmd.startswith('FS'):
                    m = re.search(r'X(\d)(\d)', cmd); fs = (int(m.group(1)), int(m.group(2)))
                elif cmd.startswith('MO'):
                    unit = 25.4 if 'IN' in cmd else 1.0
                elif cmd.startswith('AM'):
                    macros.add(cmd[2:]); break  # Makrorumpf überspringen
                elif cmd.startswith('AD'):
                    m = re.match(r'ADD(\d+)([A-Za-z_0-9]+),?(.*)', cmd)
                    n, kind, params = int(m.group(1)), m.group(2), m.group(3)
                    vals = [float(v) * unit for v in params.split('X')] if params else [0.0]
                    if kind == 'P':
                        vals = [float(v) for v in params.split('X')]; vals[0] *= unit
                    if kind in macros and kind != 'OC8':
                        raise ValueError('Aperture-Makro %s nicht unterstützt' % kind)
                    aps[n] = (kind, vals)
                elif cmd.startswith('LP'):
                    pol = cmd[2]
                    if pol != polarity:
                        polarity = pol; groups.append([pol, []])
            continue
        w = tok[:-1]
        if w.startswith('G04') or not w:
            continue
        for g in re.findall(r'G(\d+)', w):
            g = int(g)
            if g in (1, 2, 3): mode = 'G0%d' % g
            elif g == 36: region = True; reg_pts = []; reg_polys = []
            elif g == 37:
                if len(reg_pts) >= 3: reg_polys.append(reg_pts)
                for pts in reg_polys:
                    pg = Polygon(pts).buffer(0)
                    add(pg)
                region = False; reg_pts = []
            elif g in (74, 75): quadrant = 'G%d' % g
        m = re.search(r'D(\d+)$', w)
        d = int(m.group(1)) if m else None
        if d is not None and d >= 10:
            cur_ap = d; continue
        if w.startswith('M02'):
            break

        def coord(c, old):
            mm = re.search(c + r'([+-]?\d+)', w)
            if not mm: return old
            s = mm.group(1); neg = s.startswith('-'); s = s.lstrip('+-')
            v = int(s) / 10 ** fs[1]
            return (-v if neg else v) * unit
        nx, ny = coord('X', x), coord('Y', y)
        ii, jj = coord('I', 0.0), coord('J', 0.0)
        if d is None and ('X' in w or 'Y' in w):
            d = 1  # modaler D01
        if d is None:
            continue
        if mode in ('G02', 'G03') and d == 1:
            cx, cy = x + ii, y + jj
            r = math.hypot(x - cx, y - cy)
            a0 = math.atan2(y - cy, x - cx); a1 = math.atan2(ny - cy, nx - cx)
            if mode == 'G03':
                while a1 <= a0: a1 += 2 * math.pi
            else:
                while a1 >= a0: a1 -= 2 * math.pi
            n = max(4, int(abs(a1 - a0) * 64 / (2 * math.pi)))
            pts = [(cx + r * math.cos(a0 + (a1 - a0) * k / n), cy + r * math.sin(a0 + (a1 - a0) * k / n)) for k in range(1, n + 1)]
        else:
            pts = [(nx, ny)]
        if d == 1:
            if region:
                if not reg_pts: reg_pts = [(x, y)]
                reg_pts.extend(pts)
            else:
                shape = _aperture_shape(aps[cur_ap]) if cur_ap in aps else None
                prev = (x, y)
                for p in pts:
                    if shape is None:
                        lines0.append((prev, p))
                    elif aps[cur_ap][0] == 'C':
                        add(LineString([prev, p]).buffer(aps[cur_ap][1][0] / 2, quad_segs=QS))
                    else:
                        add(_stroke(shape, prev, p))
                    prev = p
        elif d == 2:
            if region and len(reg_pts) >= 3:
                reg_polys.append(reg_pts)
            reg_pts = []
        elif d == 3:
            shape = _aperture_shape(aps[cur_ap])
            if shape is not None:
                add(affinity.translate(shape, nx, ny))
        x, y = nx, ny

    geom = Polygon()
    for pol, gs in groups:
        if not gs: continue
        u = unary_union(gs)
        geom = geom.union(u) if pol == 'D' else geom.difference(u)
    return geom, lines0


def outline_polygon(lines0, geom=None):
    """Board-Umriss aus Nullbreiten-Linien (oder Linien-Geometrie) -> größtes Polygon + Innenlöcher."""
    from shapely.ops import polygonize, linemerge
    segs = [LineString([a, b]) for a, b in lines0 if a != b]
    if geom is not None and not geom.is_empty:
        segs.append(geom.boundary)
    polys = list(polygonize(unary_union(segs)))
    if not polys:
        raise ValueError('Kein geschlossener Umriss gefunden')
    polys.sort(key=lambda p: -p.area)
    outer = polys[0]
    inner = [p for p in polys[1:] if outer.contains(p.representative_point())]
    return outer, inner


def read_excellon(text):
    """Liefert Liste (Durchmesser_mm, x_mm, y_mm, 'hole') bzw. (..., 'slot', x2, y2).
    Versteht EAGLE/Fusion/KiCad-Excellon: METRIC/INCH,LZ/TZ, ;FILE_FORMAT=a:b, M71/M72,
    Werkzeugdefinition T01C0.8 / T1F00S00C0.800, Koordinaten mit oder ohne Dezimalpunkt, G85-Langloch."""
    unit = 1.0; tools = {}; cur = None; holes = []
    intd, decd = 3, 3; zeros = 'TZ'
    x = y = 0.0
    for raw in text.splitlines():
        l = raw.strip()
        if not l:
            continue
        m = re.search(r'FILE_FORMAT\s*=\s*(\d+):(\d+)', l)
        if m:
            intd, decd = int(m.group(1)), int(m.group(2)); continue
        if l.startswith(';'):
            continue
        if l.startswith('METRIC') or l.startswith('INCH'):
            unit = 25.4 if l.startswith('INCH') else 1.0
            if l.startswith('INCH'): intd, decd = 2, 4
            if ',TZ' in l: zeros = 'TZ'
            if ',LZ' in l: zeros = 'LZ'
            m = re.search(r'(0+)\.(0+)', l)
            if m: intd, decd = len(m.group(1)), len(m.group(2))
            continue
        if l == 'M71': unit = 1.0; continue
        if l == 'M72': unit = 25.4; continue
        m = re.match(r'T(\d+)(?:[FSBHZ][\d.]+)*C([\d.]+)', l)
        if m:
            tools[int(m.group(1))] = float(m.group(2)) * unit; continue
        m = re.match(r'T(\d+)$', l)
        if m:
            cur = int(m.group(1)) or None; continue

        def num(s):
            if '.' in s: return float(s) * unit
            neg = s.startswith('-'); s = s.lstrip('+-')
            # TZ = nachfolgende Nullen vorhanden -> feste Nachkommastellen
            # LZ = führende Nullen vorhanden -> rechts auffüllen
            v = int(s.ljust(intd + decd, '0')) / 10 ** decd if zeros == 'LZ' else int(s) / 10 ** decd
            return (-v if neg else v) * unit
        if (l.startswith('X') or l.startswith('Y')) and cur in tools:
            mx = re.search(r'^X([+-]?[\d.]+)', l); my = re.search(r'^(?:X[+-]?[\d.]+)?Y([+-]?[\d.]+)', l)
            if mx: x = num(mx.group(1))
            if my: y = num(my.group(1))
            g85 = re.search(r'G85X([+-]?[\d.]+)Y([+-]?[\d.]+)', l)
            if g85:
                holes.append((tools[cur], x, y, 'slot', num(g85.group(1)), num(g85.group(2))))
            else:
                holes.append((tools[cur], x, y, 'hole'))
    return holes
