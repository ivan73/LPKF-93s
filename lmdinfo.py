#!/usr/bin/env python3
"""Zeigt den Inhalt einer LMD-Datei (Phasen, Werkzeuge, Befehle)."""
import sys, collections
from lpkf93s import lmdio

h, ph = lmdio.read(open(sys.argv[1], 'rb').read())
print(h.strip())
N = {0x0c: 'Start', 0x0d: 'Linie', 0x11: 'Bogen', 0x0f: 'Bohrung'}
for p in ph:
    if not p['cmds']: continue
    names = {t[0]: t[3] for t in p['tools']}
    print('\n%s / %s' % (p['phase'], p['layer']))
    tool = None; cnt = collections.Counter()
    for op, a in p['cmds'] + [(0x10, None)]:
        if op == 0x10:
            if tool is not None:
                print('   %-34s %s' % (names[tool], ', '.join('%d %s' % (v, N[k]) for k, v in cnt.items())))
            tool = a; cnt = collections.Counter()
        else:
            cnt[op] += 1
