"""Erkennt, welche hochgeladene Datei welcher Layer ist (EAGLE, Fusion Electronics, KiCad, Altium …).

Reihenfolge: Gerber-X2-Attribut (%TF.FileFunction) > EAGLE-Ebenenname (%IN) > Dateiname/Endung > Inhalt.
"""
import os, re

ROLES = {
    'top': 'Kupfer Top',
    'bottom': 'Kupfer Bottom',
    'outline': 'Umriss',
    'drill': 'Bohrungen',
    'drill_npth': 'Bohrungen ohne DK',
    'mask_top': 'Lötstopp Top',
    'mask_bottom': 'Lötstopp Bottom',
    'ignore': 'nicht verwenden',
}

NAME_RULES = [  # (Regex auf Dateiname klein, Rolle)
    (r'npth|non.?plated|unplated', 'drill_npth'),
    (r'(\.stc$|\.gts$|soldermask_top|solder_mask_top|f[._]mask|top[._ ]?(solder)?mask)', 'mask_top'),
    (r'(\.sts$|\.gbs$|soldermask_bottom|solder_mask_bottom|b[._]mask|bottom[._ ]?(solder)?mask)', 'mask_bottom'),
    (r'(\.cmp$|\.gtl$|copper_top|f[._]cu|top[._ ]?copper|\.top$|_top\.gbr$|toplayer)', 'top'),
    (r'(\.sol$|\.gbl$|copper_bottom|b[._]cu|bottom[._ ]?copper|\.bot$|_bottom\.gbr$|bottomlayer)', 'bottom'),
    (r'(\.bor$|\.gko$|\.gm1$|\.gml$|profile|outline|edge[._]cuts|border|\.dim$|contour|mill)', 'outline'),
    (r'(\.drd$|\.drl$|\.xln$|\.exc$|\.dri$|\.txt$|drill)', 'drill'),
]
IGNORE_RULES = r'(silk|paste|plc|pls|crm|crs|gto|gbo|gtp|gbp|assembly|fab|courtyard|v-score|vsc|gbrjob|\.pdf$|\.csv$|\.json$|\.png$|\.jpg$)'


def is_excellon(text):
    head = text[:4000]
    return bool(re.search(r'^\s*M48\s*$', head, re.M)) or bool(re.search(r'^T\d+C[\d.]+', head, re.M))


def is_gerber(text):
    return '%FS' in text[:4000] or '%MO' in text[:4000]


def detect(filename, text):
    """Liefert (rolle, begründung)."""
    name = os.path.basename(filename).lower()
    excellon = is_excellon(text)
    gerber = is_gerber(text)
    if not excellon and not gerber:
        return 'ignore', 'weder Gerber noch Excellon'

    if excellon:
        ff = re.search(r'TF\.FileFunction,(\w+)', text[:4000])
        if ff and ff.group(1).lower().startswith('nonplated'):
            return 'drill_npth', 'Excellon, Attribut NonPlated'
        if re.search(r'npth|non.?plated|unplated', name):
            return 'drill_npth', 'Excellon, Dateiname'
        return 'drill', 'Excellon-Bohrdatei'

    # Gerber X2
    ff = re.search(r'%TF\.FileFunction,([^*%]+)', text[:4000])
    if ff:
        f = ff.group(1).lower().split(',')
        if f[0] == 'copper':
            if 'top' in f: return 'top', 'X2: Copper Top'
            if 'bot' in f: return 'bottom', 'X2: Copper Bottom'
            return 'ignore', 'X2: Innenlage'
        if f[0] in ('profile', 'outline'): return 'outline', 'X2: Profile'
        if f[0] == 'soldermask':
            if 'top' in f: return 'mask_top', 'X2: Soldermask Top'
            if 'bot' in f: return 'mask_bottom', 'X2: Soldermask Bottom'
        return 'ignore', 'X2: ' + ff.group(1)

    # EAGLE-Ebenenname
    m = re.search(r'%IN([^*%]+)', text[:2000])
    if m:
        n = m.group(1).lower()
        if 'stop mask' in n or 'solder mask' in n:
            if re.search(r'\b(cmp|top)\b', n): return 'mask_top', 'EAGLE: ' + m.group(1)
            if re.search(r'\b(sol|bot|bottom)\b', n): return 'mask_bottom', 'EAGLE: ' + m.group(1)
        if re.search(r'mask|silk|paste|score|stop', n): return 'ignore', 'EAGLE: ' + m.group(1)
        if 'component' in n or n.strip() in ('top', 'top copper'): return 'top', 'EAGLE: ' + m.group(1)
        if 'solder side' in n or n.strip() in ('bottom', 'bottom copper'): return 'bottom', 'EAGLE: ' + m.group(1)
        if 'border' in n or 'dimension' in n or 'outline' in n or 'profile' in n: return 'outline', 'EAGLE: ' + m.group(1)

    if re.search(IGNORE_RULES, name):
        return 'ignore', 'Dateiname'
    for rx, role in NAME_RULES:
        if role.startswith('drill'): continue
        if re.search(rx, name):
            return role, 'Dateiname'
    return 'ignore', 'nicht erkannt'


def detect_all(files):
    """files: {name: text} -> {name: (rolle, grund)}; doppelte Kupfer-/Umrisszuordnungen werden aufgelöst."""
    res = {n: detect(n, t) for n, t in files.items()}
    for role in ('top', 'bottom', 'outline', 'mask_top', 'mask_bottom'):
        cands = [n for n, (r, _) in res.items() if r == role]
        if len(cands) > 1:
            # bevorzugt: kürzester Pfad (Fusion legt Kopien in Unterordnern ab), sonst erster
            keep = sorted(cands, key=lambda n: (n.count('/'), len(n)))[0]
            for n in cands:
                if n != keep:
                    res[n] = ('ignore', 'doppelt zu ' + os.path.basename(keep))
    return res
