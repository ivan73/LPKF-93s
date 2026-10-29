#!/usr/bin/env python3
"""Weboberfläche für gerber2lmd – läuft z.B. auf einem Raspberry Pi im Schulnetz.

Start:  python3 web/app.py            (Port 8093, alle Schnittstellen)
Umgebungsvariablen: LPKF_PORT, LPKF_HOST, LPKF_DATA (Ablage der Aufträge), LPKF_KEEP (Anzahl Aufträge)
"""
import io, json, os, re, shutil, sys, threading, time, uuid, zipfile
from dataclasses import asdict

from flask import Flask, abort, jsonify, render_template, request, send_file

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from lpkf93s import core, check, detect, preview  # noqa: E402

DATA = os.path.abspath(os.environ.get('LPKF_DATA', os.path.join(os.path.dirname(__file__), '..', 'data')))
KEEP = int(os.environ.get('LPKF_KEEP', '60'))
JOBS = os.path.join(DATA, 'jobs')
os.makedirs(JOBS, exist_ok=True)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 40 * 1024 * 1024
work_lock = threading.Lock()          # der Pi rechnet immer nur einen Auftrag gleichzeitig
progress_lock = threading.Lock()
PROGRESS = {}                         # jid -> dict(state, frac, text, started)
MAX_FILE = 15 * 1024 * 1024


# ---------------------------------------------------------------- Ablage
def job_dir(jid):
    if not re.fullmatch(r'[0-9a-f]{12}', jid or ''):
        abort(404)
    d = os.path.join(JOBS, jid)
    if not os.path.isdir(d):
        abort(404)
    return d


def load_meta(jid):
    with open(os.path.join(job_dir(jid), 'meta.json'), encoding='utf-8') as f:
        return json.load(f)


def save_meta(jid, meta):
    p = os.path.join(JOBS, jid, 'meta.json')
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    os.replace(p + '.tmp', p)


def prune():
    js = sorted(os.listdir(JOBS), key=lambda j: os.path.getmtime(os.path.join(JOBS, j)), reverse=True)
    for j in js[KEEP:]:
        shutil.rmtree(os.path.join(JOBS, j), ignore_errors=True)


def safe_rel(name):
    parts = [p for p in re.split(r'[\\/]+', name) if p not in ('', '.', '..')]
    parts = [re.sub(r'[^\w.\- ]', '_', p) for p in parts]
    return '/'.join(parts)


def short_name(s, maxlen=24):
    s = re.sub(r'\.(zip|lmd)$', '', s, flags=re.I)
    s = re.sub(r'[^A-Za-z0-9_\-]+', '_', s).strip('_')
    return (s or 'platine')[:maxlen]


# ---------------------------------------------------------------- Seiten
@app.get('/')
def index():
    return render_template('index.html', tools=core.tool_choices(), tool_dia=core.tool_diameters(),
                           defaults=asdict(core.Options()),
                           roles=detect.ROLES)


@app.get('/api/jobs')
def jobs():
    out = []
    for j in sorted(os.listdir(JOBS), key=lambda j: os.path.getmtime(os.path.join(JOBS, j)), reverse=True):
        try:
            m = load_meta(j)
        except Exception:
            continue
        out.append(dict(id=j, name=m.get('name'), created=m.get('created'), status=m.get('status'),
                        ok=m.get('verify_ok'), lmd=m.get('lmd_name')))
    return jsonify(out)


@app.post('/api/upload')
def upload():
    files = request.files.getlist('files')
    if not files:
        return jsonify(error='Keine Datei empfangen.'), 400
    jid = uuid.uuid4().hex[:12]
    d = os.path.join(JOBS, jid, 'files'); os.makedirs(d)
    stored = {}
    project = None

    def keep(rel, data):
        if len(data) > MAX_FILE: return
        rel = safe_rel(rel)
        if not rel: return
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'wb') as f: f.write(data)
        stored[rel] = data.decode('latin1')

    for f in files:
        raw = f.read()
        if f.filename.lower().endswith('.zip') or raw[:4] == b'PK\x03\x04':
            project = project or os.path.splitext(os.path.basename(f.filename))[0]
            try:
                z = zipfile.ZipFile(io.BytesIO(raw))
            except zipfile.BadZipFile:
                shutil.rmtree(os.path.join(JOBS, jid)); return jsonify(error='%s ist kein gültiges ZIP.' % f.filename), 400
            for zi in z.infolist():
                if zi.is_dir() or zi.file_size > MAX_FILE: continue
                keep(zi.filename, z.read(zi))
        else:
            keep(f.filename, raw)
    if not stored:
        shutil.rmtree(os.path.join(JOBS, jid)); return jsonify(error='Keine verwertbaren Dateien gefunden.'), 400

    # Projektname: Gerber-Jobdatei (Fusion/EAGLE) > ZIP-Name > Kupferdatei
    for n, t in stored.items():
        if n.lower().endswith('.gbrjob'):
            try:
                pid = json.loads(t)['Overall']['Name']['ProjectId']
                project = project or pid
                break
            except Exception:
                pass
    det = detect.detect_all(stored)
    if not project:
        cu = [n for n, (r, _) in det.items() if r in ('top', 'bottom', 'outline')]
        project = os.path.basename(cu[0]).rsplit('.', 1)[0] if cu else 'platine'
    meta = dict(id=jid, name=project, created=time.strftime('%Y-%m-%d %H:%M'), status='hochgeladen',
                files=[dict(name=n, role=r, reason=w, size=len(stored[n])) for n, (r, w) in sorted(det.items())],
                lmd_name=short_name(project) + '.LMD')
    save_meta(jid, meta)
    prune()
    return jsonify(meta)


@app.get('/api/job/<jid>')
def job(jid):
    return jsonify(load_meta(jid))


class InputError(Exception):
    pass


def _build(jid, body):
    """Eingaben und Optionen aus der Anfrage zusammenbauen (gemeinsam für Vorprüfung und Berechnung)."""
    meta = load_meta(jid)
    roles = body.get('roles', {})
    o = body.get('options', {})
    d = os.path.join(job_dir(jid), 'files')
    rd = lambda n: open(os.path.join(d, safe_rel(n)), encoding='latin1').read()

    pick = {r: [n for n, rr in roles.items() if rr == r] for r in detect.ROLES}
    for r in ('top', 'bottom', 'outline', 'mask_top', 'mask_bottom'):
        if len(pick[r]) > 1:
            raise InputError('%s ist mehrfach zugeordnet.' % detect.ROLES[r])
    if not pick['outline']:
        raise InputError('Bitte eine Datei als Platinenumriss zuordnen.')
    if not pick['top'] and not pick['bottom']:
        raise InputError('Bitte mindestens eine Kupferlage zuordnen.')

    def num(k, typ, lo, hi, default):
        try: v = typ(o.get(k, default))
        except (TypeError, ValueError): v = default
        return max(lo, min(hi, v))
    dflt = core.Options()
    opt = core.Options(
        iso_tool=o.get('iso_tool') or dflt.iso_tool,
        iso_width=num('iso_width', float, 0.0, 5.0, dflt.iso_width),
        pad_width=num('pad_width', float, 0.0, 5.0, 0.0) if o.get('pads') else 0.0,
        passes=num('passes', int, 1, 20, dflt.passes),
        overlap=num('overlap', float, 0.0, 0.9, dflt.overlap),
        rubout_tool=o.get('rubout_tool', dflt.rubout_tool) if o.get('rubout', True) else '',
        cut_tool=o.get('cut_tool') or dflt.cut_tool,
        tabs=num('tabs', int, 0, 12, dflt.tabs),
        tab_width=num('tab_width', float, 0.2, 5.0, dflt.tab_width),
        marking=bool(o.get('marking', True)),
        mark_tool=o.get('mark_tool') or dflt.mark_tool,
        drill_map={str(k): str(v) for k, v in (o.get('drill_map') or {}).items() if v and v != 'auto'})
    inp = core.Inputs(top=rd(pick['top'][0]) if pick['top'] else None,
                      bottom=rd(pick['bottom'][0]) if pick['bottom'] else None,
                      outline=rd(pick['outline'][0]),
                      drills=[(rd(n), True) for n in pick['drill']] + [(rd(n), False) for n in pick['drill_npth']],
                      mask_top=rd(pick['mask_top'][0]) if pick['mask_top'] else None,
                      mask_bottom=rd(pick['mask_bottom'][0]) if pick['mask_bottom'] else None)
    lmd_name = short_name(body.get('lmd_name') or meta['lmd_name']) + '.LMD'
    return meta, inp, opt, roles, lmd_name


def _copper_json(copper):
    """Kupfer je Seite für die Vektoransicht (vereinfacht, je Polygon: Außenring + Löcher als flache Koordinatenlisten)."""
    out = {}
    for side, geom in copper.items():
        out[side] = [[[round(v, 3) for xy in r.coords for v in xy] for r in [pg.exterior] + list(pg.interiors)]
                     for pg in core.polys(geom.simplify(0.003))]
    return out


@app.post('/api/job/<jid>/precheck')
def job_precheck(jid):
    """Vorprüfung ohne Fräsweg-Berechnung: Engstellen, Bohrungen, Restring. Dauert nur Sekunden."""
    try:
        meta, inp, opt, roles, lmd_name = _build(jid, request.get_json(force=True))
        pc = core.precheck(inp, opt)
    except InputError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:
        return jsonify(error='Vorprüfung fehlgeschlagen: %s' % e), 422
    b = pc['board']
    x0, y0, x1, y1 = b.bounds
    view = dict(bounds=[x0, y0, x1, y1], board=[[round(x, 3), round(y, 3)] for x, y in b.exterior.coords],
                copper=_copper_json(pc['copper']),
                phases=[dict(phase='Milling' + s, layer='Insulate' + s, label='Kupfer %s (Vorprüfung)' % s, tools={}, items=[])
                        for s in ('Top', 'Bottom') if s in pc['copper']])
    return jsonify(items=pc['items'], suggestion=pc['suggestion'], view=view)


@app.post('/api/job/<jid>/generate')
def generate(jid):
    try:
        meta, inp, opt, roles, lmd_name = _build(jid, request.get_json(force=True))
    except InputError as e:
        return jsonify(error=str(e)), 400

    with progress_lock:
        st = PROGRESS.get(jid)
        if st and st['state'] in ('wartet', 'rechnet'):
            return jsonify(error='Dieser Auftrag wird bereits berechnet.'), 409
        PROGRESS[jid] = dict(state='wartet', frac=0.0, text='wartet auf freien Rechner …', started=time.time())
    meta.update(status='rechnet'); save_meta(jid, meta)
    threading.Thread(target=_run_job, args=(jid, inp, opt, roles, lmd_name), daemon=True).start()
    return jsonify(started=True), 202


def _set_progress(jid, **kw):
    with progress_lock:
        PROGRESS.setdefault(jid, {}).update(kw)


def _run_job(jid, inp, opt, roles, lmd_name):
    """Berechnung im Hintergrund; der Fortschritt wird über /api/job/<id>/progress abgefragt."""
    with work_lock:                      # der Pi rechnet immer nur einen Auftrag gleichzeitig
        t0 = time.time()
        _set_progress(jid, state='rechnet', started=t0, frac=0.0, text='Start')
        rep = lambda a, b: (lambda f, t: _set_progress(jid, frac=round(a + (b - a) * f, 4), text=t))
        meta = load_meta(jid)
        try:
            res = core.generate(inp, opt, progress=rep(0.0, 0.75))
            ok, lines = check.verify(res.lmd, inp.top, inp.bottom, inp.outline, [t for t, _ in inp.drills],
                                     progress=rep(0.75, 0.90))
            pngs = {}
            sides = preview.sides_present(res.lmd)
            for k, s in enumerate(sides):
                _set_progress(jid, frac=round(0.90 + 0.09 * k / len(sides), 4), text='Vorschau %s' % ('Top' if s == 'MillingTop' else 'Bottom'))
                pngs[s] = preview.render(res.lmd, res.board, dpi=110, only=s)
        except Exception as e:  # Fehler in Eingabedaten verständlich zurückgeben
            meta.update(status='Fehler', error=str(e)); save_meta(jid, meta)
            _set_progress(jid, state='fehler', text='Verarbeitung fehlgeschlagen: %s' % e)
            return

        jd = os.path.join(JOBS, jid)
        with open(os.path.join(jd, 'result.LMD'), 'wb') as f: f.write(res.lmd)
        for s, png in pngs.items():
            with open(os.path.join(jd, 'preview_%s.png' % s), 'wb') as f: f.write(png)
        cu_json = _copper_json(res.copper)
        with open(os.path.join(jd, 'copper.json'), 'w') as f:
            json.dump(cu_json, f, separators=(',', ':'))
        with open(os.path.join(jd, 'board.json'), 'w') as f:
            json.dump([[round(x, 3), round(y, 3)] for x, y in res.board.exterior.coords], f)
        seqp = os.path.join(jd, 'sequence.json')
        if os.path.exists(seqp): os.remove(seqp)
        for fm in meta['files']:
            fm['role'] = roles.get(fm['name'], fm['role'])
        meta.update(status='fertig', lmd_name=lmd_name, sides=list(pngs), options=asdict(opt), log=res.log, warnings=res.warnings,
                    verify=[dict(ok=g, text=t, loc=l) for g, t, l in lines], verify_ok=ok, stats=res.stats,
                    marks=res.marks, seconds=round(time.time() - t0, 1), error=None)
        save_meta(jid, meta)
        _set_progress(jid, state='fertig', frac=1.0, text='fertig')


@app.get('/api/job/<jid>/progress')
def job_progress(jid):
    job_dir(jid)
    with progress_lock:
        st = dict(PROGRESS.get(jid) or {})
    if not st:
        m = load_meta(jid)
        st = dict(state={'fertig': 'fertig', 'Fehler': 'fehler'}.get(m.get('status'), 'unbekannt'),
                  frac=1.0 if m.get('status') == 'fertig' else 0.0, text=m.get('error') or m.get('status', ''))
    if 'started' in st:
        st['elapsed'] = round(time.time() - st.pop('started'), 1)
    return jsonify(st)


@app.get('/job/<jid>/preview/<side>.png')
def job_preview(jid, side):
    if side not in ('MillingTop', 'MillingBottom'): abort(404)
    p = os.path.join(job_dir(jid), 'preview_%s.png' % side)
    if not os.path.exists(p): abort(404)
    return send_file(p, mimetype='image/png', max_age=0)


@app.post('/api/job/<jid>/drillinfo')
def job_drillinfo(jid):
    """Bohrübersicht je Durchmesser für die aktuelle Lagenzuordnung (Via-Hinweis, Restring, Standardbohrer)."""
    d = os.path.join(job_dir(jid), 'files')
    roles = (request.get_json(force=True) or {}).get('roles', {})
    rd = lambda n: open(os.path.join(d, safe_rel(n)), encoding='latin1').read()
    pick = lambda r: [n for n, rr in roles.items() if rr == r]
    drills = [(rd(n), True) for n in pick('drill')] + [(rd(n), False) for n in pick('drill_npth')]
    top = rd(pick('top')[0]) if pick('top') else None
    bottom = rd(pick('bottom')[0]) if pick('bottom') else None
    try:
        return jsonify(core.drill_summary(drills, top, bottom))
    except Exception as e:
        return jsonify(error=str(e)), 422


@app.get('/api/job/<jid>/sequence')
def job_sequence(jid):
    """Ablauf (Reihenfolge der Bahnen/Bohrungen) für den Player – einmal berechnet, dann zwischengespeichert."""
    jd = job_dir(jid)
    p = os.path.join(jd, 'sequence.json')
    if not os.path.exists(p):
        lmd = os.path.join(jd, 'result.LMD')
        if not os.path.exists(lmd): abort(404)
        seq = preview.sequence(open(lmd, 'rb').read())
        bp = os.path.join(jd, 'board.json')
        seq['board'] = json.load(open(bp)) if os.path.exists(bp) else None
        cp = os.path.join(jd, 'copper.json')
        seq['copper'] = json.load(open(cp)) if os.path.exists(cp) else {}
        with open(p + '.tmp', 'w') as f: json.dump(seq, f, separators=(',', ':'))
        os.replace(p + '.tmp', p)
    return send_file(p, mimetype='application/json', max_age=0)


@app.get('/job/<jid>/download')
def job_download(jid):
    meta = load_meta(jid)
    p = os.path.join(job_dir(jid), 'result.LMD')
    if not os.path.exists(p): abort(404)
    return send_file(p, mimetype='application/octet-stream', as_attachment=True, download_name=meta['lmd_name'])


@app.post('/api/job/<jid>/delete')
def job_delete(jid):
    shutil.rmtree(job_dir(jid), ignore_errors=True)
    return jsonify(ok=True)


if __name__ == '__main__':
    host = os.environ.get('LPKF_HOST', '0.0.0.0'); port = int(os.environ.get('LPKF_PORT', '8093'))
    try:
        from waitress import serve          # apt install python3-waitress (empfohlen auf dem Pi)
        print('gerber2lmd-Web auf http://%s:%d (waitress)' % (host, port), flush=True)
        serve(app, host=host, port=port, threads=4)
    except ImportError:
        app.run(host=host, port=port, threaded=True)
