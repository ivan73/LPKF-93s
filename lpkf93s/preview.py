"""Vorschau einer LMD-Datei als PNG (Top / Bottom nebeneinander)."""
import io, math
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from . import lmdio


def arcpts(p0, x, y, cx, cy, r):
    a0 = math.atan2(p0[1] - cy, p0[0] - cx); a1 = math.atan2(y - cy, x - cx)
    if r < 0:
        while a1 >= a0: a1 -= 2 * math.pi
    else:
        while a1 <= a0: a1 += 2 * math.pi
    R = abs(r); k = max(2, int(abs(a1 - a0) / 0.1) + 1)
    return [(cx + R * math.cos(a0 + (a1 - a0) * t / k), cy + R * math.sin(a0 + (a1 - a0) * t / k)) for t in range(1, k + 1)]


def paths(cmds):
    """LMD-Befehle -> ([(werkzeug, punktliste)], [(werkzeug, (x, y))]) in Metern."""
    out = []; cur = None; pl = None; tool = None; drills = []
    for op, a in cmds:
        if op == 0x10: tool = a; continue
        if op == 0x0c: pl = [a[:2]]; out.append((tool, pl)); cur = a[:2]
        elif op == 0x0d: pl.append(a[:2]); cur = a[:2]
        elif op == 0x11: pl.extend(arcpts(cur, *a)); cur = a[:2]
        elif op == 0x0f: drills.append((tool, a[:2]))
    return out, drills


SIDES = (('MillingTop', 'Bestückungsseite (Top)'), ('MillingBottom', 'Lötseite (Bottom, ungespiegelt)'))


def sides_present(lmd_bytes):
    _, phases = lmdio.read(lmd_bytes)
    has = {p['phase'] for p in phases if p['cmds']}
    return [s[0] for s in SIDES if s[0] in has] or ['MillingTop']


def render(lmd_bytes, board=None, dpi=130, only=None):
    """Liefert PNG-Bytes. only='MillingTop'/'MillingBottom' = nur diese Seite, groß und randlos."""
    _, phases = lmdio.read(lmd_bytes)
    present = sides_present(lmd_bytes)
    sides = [s for s in SIDES if s[0] in (only and [only] or present)]
    if board is not None:
        bx0, by0, bx1, by1 = board.bounds
        h = 10 * (by1 - by0 + 6) / (bx1 - bx0 + 6)
    else:
        h = 10.5
    fig, axs = plt.subplots(1, len(sides), figsize=(10 * len(sides), h if only else 10.5), squeeze=False)
    for ax, (mill, title) in zip(axs[0], sides):
        for p in phases:
            if not p['cmds']: continue
            if not (p['phase'] == mill or p['phase'] in ('DrillingPlated', 'DrillingUnplated', 'CuttingOutside')):
                continue
            tdia = {t[0]: t[2] * 1e3 for t in p['tools']}
            pl, dr = paths(p['cmds'])
            for t, pts in pl:
                xs, ys = zip(*pts)
                if p['phase'] == 'CuttingOutside': col, lw = '#1f6f2f', 0.9
                elif p['phase'].startswith('Drilling'): col, lw = '#7a1fa2', 0.8
                else: col, lw = ('#d0342c', 0.35) if tdia[t] < 0.5 else ('#2a6fdb', 0.35)
                ax.plot([v * 1e3 for v in xs], [v * 1e3 for v in ys], lw=lw, color=col)
            for t, (x, y) in dr:
                ax.add_patch(plt.Circle((x * 1e3, y * 1e3), tdia[t] / 2, fill=False,
                                        color='k' if p['phase'] == 'DrillingPlated' else '#b06000', lw=0.5))
        if board is not None:
            bx, by = board.exterior.xy; ax.plot(bx, by, color='#999', lw=0.5, ls='--')
        if not only: ax.set_title(title)
        ax.set_aspect('equal'); ax.grid(alpha=0.2); ax.tick_params(labelsize=8)
    axs[0][0].legend(handles=[Line2D([], [], color='#d0342c', label='Isolation'),
                              Line2D([], [], color='#2a6fdb', label='Ausräumen'),
                              Line2D([], [], color='k', label='Bohrung'),
                              Line2D([], [], color='#7a1fa2', label='gefräste Bohrung'),
                              Line2D([], [], color='#1f6f2f', label='Außenkontur')], loc='upper left', fontsize=8)
    plt.tight_layout()
    buf = io.BytesIO(); plt.savefig(buf, format='png', dpi=dpi, bbox_inches='tight', pad_inches=0.1); plt.close(fig)
    return buf.getvalue()
