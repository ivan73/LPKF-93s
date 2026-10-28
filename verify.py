#!/usr/bin/env python3
"""Prüft eine LMD-Datei gegen Gerber/Excellon: Kupferabstand, Kurzschluss, Vollständigkeit der Bohrungen.
Aufruf: verify.py datei.LMD --top x.cmp --bottom x.sol --outline x.bor --drill x.drd"""
import argparse, sys
from lpkf93s import check

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('lmd'); ap.add_argument('--top'); ap.add_argument('--bottom'); ap.add_argument('--outline')
ap.add_argument('--drill', action='append', default=[])
a = ap.parse_args()
rd = lambda f: open(f, encoding='latin1').read() if f else None
ok, lines = check.verify(open(a.lmd, 'rb').read(), rd(a.top), rd(a.bottom), rd(a.outline), [rd(f) for f in a.drill])
for good, l in lines: print('OK    ' if good else 'FEHLER', l)
print('ERGEBNIS:', 'alles in Ordnung' if ok else 'PROBLEME gefunden')
sys.exit(0 if ok else 2)
