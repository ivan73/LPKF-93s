"""Kompatibilität Shapely 1.7/1.8 (Debian Bullseye/Bookworm, Raspberry Pi OS) und Shapely 2.x.

Unterschiede, die hier abgefangen werden:
  * buffer(): Shapely 2 heißt der Parameter quad_segs, Shapely 1 resolution
    -> immer positional übergeben (zweites Argument ist in beiden die Segmentzahl)
  * STRtree.query(): Shapely 2 liefert Indizes, Shapely 1 die Geometrien selbst
"""
import warnings
import shapely
from shapely.strtree import STRtree

SHAPELY2 = int(shapely.__version__.split('.')[0]) >= 2
if not SHAPELY2:
    # Shapely 1.8 warnt bei jeder STRtree-Nutzung vor 2.0 – hier abgefangen, also stumm schalten
    try:
        from shapely.errors import ShapelyDeprecationWarning
        warnings.filterwarnings('ignore', category=ShapelyDeprecationWarning)
    except ImportError:
        pass


def buf(g, dist, segs=16):
    """Puffer mit 'segs' Segmenten je Viertelkreis – in Shapely 1 und 2 gleich."""
    return g.buffer(dist, segs)


class Index:
    """Räumlicher Index über eine Geometrieliste; query() liefert immer Indizes."""

    def __init__(self, geoms):
        self.geoms = list(geoms)
        self.tree = STRtree(self.geoms) if self.geoms else None
        if not SHAPELY2:
            self.ids = {id(g): i for i, g in enumerate(self.geoms)}

    def query(self, g):
        if self.tree is None:
            return []
        res = self.tree.query(g)
        if SHAPELY2:
            return [int(i) for i in res]
        out = []
        for r in res:
            i = self.ids.get(id(r))
            if i is None:  # sollte nicht vorkommen – Rückfall auf Gleichheit
                i = next((k for k, x in enumerate(self.geoms) if x.equals(r)), None)
            if i is not None:
                out.append(i)
        return out
