"""Authenticated internal workflow UI; no third-party scripts or data services."""
import io
import json
import secrets
import stat
import shutil
import time
from functools import wraps
from pathlib import Path

import numpy as np
import pydicom
from flask import Flask, Response, abort, jsonify, render_template, request, session, send_from_directory
from PIL import Image
from werkzeug.security import check_password_hash

import engine
import patient_identity
import sql_gui_candidates
import sql_association
import sql_prepare_review
from report_format import format_report
from ohif_poc.clinical_dicomweb import create_blueprint as ohif_blueprint, viewer_authorized_uid, authorized_uid
from store import audit, database, enqueue, initialize, token


SQL_REVIEW_GATE = Path("/etc/hoag-research/sql-candidate-review.enabled")


def sql_review_permitted():
    """Fail closed until independently audited SQL identity matching is approved."""
    try:
        if SQL_REVIEW_GATE.is_symlink() or not SQL_REVIEW_GATE.is_file():
            return False
        mode = SQL_REVIEW_GATE.stat()
        if mode.st_uid != 0 or mode.st_size > 32 or mode.st_mode & 0o022:
            return False
        return SQL_REVIEW_GATE.read_text().strip() == "AUTHORIZED"
    except OSError:
        return False


def create_app(config):
    initialize(config)
    app = Flask(__name__)
    app.secret_key = config['secret_key']
    app.config.update(MAX_CONTENT_LENGTH=12 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Strict', SESSION_COOKIE_SECURE=config.get('secure_cookies', True))

    app.register_blueprint(ohif_blueprint(config))

    @app.before_request
    def protect():
        auth = request.authorization
        if not auth or auth.username != config['username'] or not check_password_hash(config['password_hash'], auth.password or ''):
            return Response('Authentication required', 401, {'WWW-Authenticate': 'Basic realm="HOAG Research"'})
        if request.method == 'POST' and not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), session.get('csrf', 'missing')):
            abort(403)

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        if request.path == '/viewer' or request.path == '/app-config.js' or request.path.endswith(('.js', '.css', '.wasm', '.svg', '.woff2')):
            response.headers['X-Frame-Options'] = 'SAMEORIGIN'
            # The bundled OHIF libjpeg-turbo Emscripten worker calls new Function()
            # while decoding JPEG Lossless. Keep this relaxation restricted to
            # OHIF viewer/assets, never the main HOAG dashboard HTML.
            # Hashes allow only the two known inline OHIF bootstrap scripts.
            response.headers['Content-Security-Policy'] = (
                "default-src 'self' blob: data:; "
                "script-src 'self' 'unsafe-eval' 'wasm-unsafe-eval' "
                "'sha256-RUJKPsAHcLA3YS8dWuPEiqEPTlkfTEHKow0zXWj7MBE=' "
                "'sha256-YGQALmFLH0ots68+CN8MF4DamHt77VywRO0d1cRTm3Y='; "
                "worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' blob: data:; font-src 'self' data:; "
                "connect-src 'self'; frame-ancestors 'self'"
            )
        else:
            response.headers['X-Frame-Options'] = 'DENY'
            response.headers['Content-Security-Policy'] = ("default-src 'self'; style-src 'self'; script-src 'self'; "
                "img-src 'self' blob:; frame-src 'self'; frame-ancestors 'none'")
        return response

    @app.errorhandler(ValueError)
    def validation(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(500)
    def failure(error):
        return jsonify(error='Request failed; check source availability and service status'), 500

    @app.get('/')
    def index():
        session.setdefault('csrf', secrets.token_urlsafe(32))
        return render_template('index.html', csrf=session['csrf'])

    OHIF_ASSETS = Path('/opt/hoag-research/ohif-assets')

    @app.get('/viewer')
    def ohif_viewer():
        viewer_authorized_uid(config, request.args.get('StudyInstanceUIDs', ''))
        if not (OHIF_ASSETS / 'index.html').is_file():
            abort(404)
        return send_from_directory(OHIF_ASSETS, 'index.html')

    @app.get('/<path:ohif_asset>')
    def ohif_static(ohif_asset):
        if not ohif_asset.endswith(('.js', '.css', '.wasm', '.woff', '.woff2',
                                     '.svg', '.png', '.ico', '.json', '.map')):
            abort(404)
        if not OHIF_ASSETS.is_dir():
            abort(404)
        return send_from_directory(OHIF_ASSETS, ohif_asset)

    @app.get('/api/study/<uid>/ohif-status')
    def ohif_status(uid):
        try:
            authorized_uid(config, uid)
        except Exception:
            return jsonify(enabled=False)
        return jsonify(enabled=(OHIF_ASSETS / 'index.html').is_file())

    @app.get('/api/historical-emr/candidates')
    def historical_emr_candidates():
        import historical_emr_review
        if not historical_emr_review.permitted():
            abort(404)
        return jsonify(historical_emr_review.listing(config))

    @app.get('/api/historical-emr/candidate/<candidate_token>')
    def historical_emr_candidate(candidate_token):
        import historical_emr_review
        if not historical_emr_review.permitted():
            abort(404)
        try:
            result=historical_emr_review.preview(config,candidate_token)
        except LookupError:
            abort(404)
        return jsonify(result)

    @app.get('/api/status')
    def status():
        if config.get('config_path'):
            from store import load_config
            fresh=load_config(config['config_path'])
            config['source_roots']=fresh['source_roots']
            config['local_source_roots']=fresh.get('local_source_roots',[])
        with database(config) as db:
            counts = [dict(r) for r in db.execute('SELECT kind,status,count(*) count,sum(size) bytes FROM files WHERE active=1 GROUP BY kind,status')]
            states = [dict(r) for r in db.execute('SELECT state,count(*) count FROM studies GROUP BY state')]
            jobs = [dict(r) for r in db.execute("SELECT id,kind,state,progress,message,created,started,finished,phase,cancel_requested,json_extract(payload,'$.uid') study_uid FROM jobs ORDER BY id DESC LIMIT 20")]
            sql = [dict(r) for r in db.execute("SELECT id,path,metadata FROM files WHERE kind='sql' AND active=1 AND status='ok'")]
            exports = [dict(r) for r in db.execute('SELECT id,folder,created,bytes,validated,validation FROM exports ORDER BY id DESC LIMIT 100')]
            settings = dict(db.execute('SELECT key,value FROM settings'))
            modalities = [r[0] for r in db.execute("SELECT DISTINCT modality FROM studies ORDER BY modality")]
        version = Path(__file__).with_name('VERSION').read_text().strip()
        return jsonify(version=version, counts=counts, states=states, jobs=jobs, sql=sql, exports=exports, settings=settings, modalities=modalities, now=time.time(),
                       sources=config['source_roots'], output=config['output_root'])

    @app.post('/api/exports/crosswalk')
    def download_crosswalk():
        # Existing authenticated hospital operator + CSRF protection applies.
        with database(config) as db:
            content = patient_identity.crosswalk_csv(db)
            audit(db, config['username'], 'download-patient-crosswalk')
        return Response(content, content_type='text/csv; charset=utf-8', headers={
            'Content-Disposition': 'attachment; filename="patient-crosswalk.csv"'})

    @app.get('/api/studies')
    def studies():
        q = '%' + request.args.get('q', '')[:200] + '%'
        offset = max(0, int(request.args.get('offset', 0)))
        clauses = ['(accession LIKE ? OR patient LIKE ? OR name LIKE ? OR subject LIKE ?)']
        params = [q] * 4
        for key, column in [('state', 'state'), ('modality', 'modality')]:
            value = request.args.get(key, '')
            if value:
                clauses.append(column + '=?')
                params.append(value[:100])
        for key, op in [('from', '>='), ('to', '<=')]:
            value = request.args.get(key, '').replace('-', '')
            if value:
                if len(value) != 8 or not value.isdigit():
                    raise ValueError('Use a valid date filter')
                clauses.append('date' + op + '?')
                params.append(value)
        sort = request.args.get('sort', 'date')
        if sort not in ('date', 'accession', 'name', 'modality', 'count', 'state'):
            raise ValueError('Invalid sort column')
        direction = 'ASC' if request.args.get('direction') == 'asc' else 'DESC'
        where = ' AND '.join(clauses)
        with database(config) as db:
            total = db.execute('SELECT count(*) FROM studies WHERE ' + where, params).fetchone()[0]
            rows = [dict(r) for r in db.execute('SELECT uid,subject,accession,patient,name,date,modality,count,state FROM studies WHERE ' + where + ' ORDER BY ' + sort + ' ' + direction + ',uid LIMIT 100 OFFSET ?', [*params, offset])]
        if request.args.get('paged') == '1':
            return jsonify(rows=rows, total=total, offset=offset, page_size=100)
        return jsonify(rows)

    @app.get('/api/source-errors')
    def source_errors():
        with database(config) as db:
            rows = [dict(r) for r in db.execute("SELECT path,kind,metadata FROM files WHERE active=1 AND status='error' ORDER BY id LIMIT 100")]
        return jsonify(rows)

    @app.get('/api/health')
    def health():
        results = []
        for index, path in enumerate([*config['source_roots'], config['output_root']]):
            output = index == len(config['source_roots'])
            item = {'slot': 'output' if output else ('SQL database' if path=='/mnt/hoag-database' else 'TXT / bulk' if index==0 else 'DICOM imaging' if index==1 else 'Local report source '+str(index-1)), 'path': path, 'output': output}
            try:
                root = engine.checked_root(config, path, output=output)
                usage = shutil.disk_usage(root)
                item.update(ok=True, message='Writable output connected' if output else 'Read-only source connected', free=usage.free, total=usage.total)
            except Exception as error:
                item.update(ok=False, message=str(error) if type(error) is ValueError else 'Share unavailable; check mount and permissions')
            results.append(item)
        return jsonify(shares=results)

    @app.post('/api/mount')
    def mount():
        from mount_service import request_mount
        body = request.get_json()
        return jsonify(request_mount(body))

    @app.get('/api/report/<int:file_id>')
    def report_text(file_id):
        with database(config) as db:
            row = db.execute("SELECT * FROM files WHERE id=? AND kind IN ('report','sql_report') AND status='ok' AND active=1", (file_id,)).fetchone()
        if not row:
            abort(404)
        return jsonify(text=engine.read_report(config, row)[0], path=row['path'])

    @app.post('/api/jobs/<int:job_id>/cancel')
    def cancel(job_id):
        with database(config) as db:
            row = db.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row or row['state'] not in ('queued', 'running'):
                raise ValueError('Job is no longer active')
            if row['state'] == 'queued':
                db.execute("UPDATE jobs SET state='cancelled',cancel_requested=1,finished=?,message='Cancelled before starting' WHERE id=?", (time.time(), job_id))
            else:
                db.execute('UPDATE jobs SET cancel_requested=1 WHERE id=?', (job_id,))
            audit(db, config['username'], 'request-cancel', str(job_id))
        return jsonify(message='Cancellation requested. The current operation finishes before the next safe boundary.')

    @app.post('/api/jobs/<int:job_id>/retry')
    def retry(job_id):
        with database(config) as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row or row['state'] not in ('failed', 'cancelled'):
            raise ValueError('Only failed or cancelled jobs can be retried')
        return jsonify(id=enqueue(config, row['kind'], json.loads(row['payload']), config['username']))

    @app.get('/api/folders')
    def folders():
        return jsonify(engine.browse_folders(config, int(request.args.get('root', 0)),
                                             request.args.get('relative', '.'), int(request.args.get('offset', 0))))

    @app.get('/api/study/<uid>/suggestions')
    def study_suggestions(uid):
        from research_matching import suggestions
        return jsonify(suggestions(config,uid))

    @app.get('/api/sql-readiness')
    def sql_readiness():
        with database(config) as db:
            dumps=[dict(r) for r in db.execute("SELECT id,path FROM files WHERE kind='sql' AND active=1 AND status='ok'")]
            job=db.execute("SELECT state,message FROM jobs WHERE kind='reindex_sql' ORDER BY id DESC LIMIT 1").fetchone()
        try:broker=sql_gui_candidates.request_broker({'action':'status'})
        except ValueError as error:broker={'available':False,'reports':None,'message':str(error)}
        return jsonify(dumps=dumps,broker=broker,last_rebuild=dict(job) if job else None)

    @app.get('/api/research-queue')
    def research_queue():
        offset=max(0,int(request.args.get('offset',0)))
        bucket=request.args.get('bucket','')
        if bucket not in ('','strong','review','differences','no suggestions'):raise ValueError('Invalid queue filter')
        join=" FROM studies d LEFT JOIN study_suggestions s ON s.uid=d.uid AND s.position=0 AND s.fingerprint=d.fingerprint WHERE d.report_id IS NULL"
        params=[]
        filtered=join
        if bucket:
            filtered+=" AND COALESCE(json_extract(s.ranking,'$.bucket'),'no suggestions')=?";params.append(bucket)
        with database(config) as db:
            ready=db.execute("SELECT value FROM settings WHERE key='suggestions_ready'").fetchone()
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') AND kind IN ('scan','reindex','reindex_sql','suggest_reports','match','add_reports') LIMIT 1").fetchone():ready=None
            total=db.execute('SELECT count(*)'+filtered,params).fetchone()[0]
            rows=[dict(r) for r in db.execute("SELECT d.uid,d.name,d.accession,d.date,d.modality,d.state,json_extract(s.ranking,'$.bucket') bucket"+filtered+" ORDER BY CASE json_extract(s.ranking,'$.bucket') WHEN 'strong' THEN 0 WHEN 'review' THEN 1 WHEN 'differences' THEN 2 ELSE 3 END,d.date DESC,d.uid LIMIT 100 OFFSET ?",params+[offset])]
            counts=[dict(r) for r in db.execute("SELECT COALESCE(json_extract(s.ranking,'$.bucket'),'no suggestions') bucket,count(*) count"+join+" GROUP BY bucket")]
        ready=bool(ready and ready[0]=='1')
        return jsonify(ready=ready,rows=rows if ready else [],counts=counts if ready else [],total=total if ready else 0)

    @app.post('/api/report-browser/search')
    def browser_search():
        from report_browser import search
        return jsonify(search(config,request.get_json(silent=True) or {}))

    @app.post('/api/report-browser/preview')
    def browser_preview():
        from report_browser import preview
        body=request.get_json(silent=True) or {}
        return jsonify(preview(config,body.get('source'),body.get('ref')))

    @app.post('/api/study/<uid>/report-association')
    def browser_association(uid):
        from report_browser import associate
        return jsonify(associate(config,uid,request.get_json(silent=True) or {},config['username']))

    @app.get('/api/reports')
    def reports():
        q = '%' + request.args.get('q', '')[:200] + '%'
        with database(config) as db:
            rows = [dict(r) for r in db.execute("SELECT id,path FROM files WHERE kind='report' AND active=1 AND status='ok' AND path LIKE ? LIMIT 100", (q,))]
        return jsonify(rows)

    @app.get('/api/study/<uid>/txt-candidates')
    def txt_candidates(uid):
        from txt_review import candidates
        return jsonify(candidates(config, uid, request.args.get('q'), request.args.get('sort','reason'), int(request.args.get('offset',0))))

    @app.get('/api/study/<uid>/txt-report/<int:report_id>')
    def txt_preview(uid,report_id):
        from txt_review import preview
        return jsonify(preview(config,uid,report_id))

    @app.post('/api/study/<uid>/txt-association')
    def txt_association(uid):
        from txt_review import associate
        body = request.get_json(silent=True) or {}
        return jsonify(associate(config,uid,int(body.get('report_id',0)),body.get('note',''),body.get('confirm'),config['username']))

    @app.get('/api/study/<uid>/sql-candidates')
    def sql_candidates(uid):
        if not sql_review_permitted():
            return jsonify(error="SQL matching is temporarily disabled for patient-identity verification"), 503
        from strict_identity_match import normalize_name
        requested_name = request.args.get("patient_name", "")
        if not isinstance(requested_name, str) or len(requested_name) > 120:
            raise ValueError("Invalid patient name search")
        trusted = sql_gui_candidates.study_request(config, uid)
        if (not normalize_name(requested_name) or
                normalize_name(requested_name) != normalize_name(trusted["name"])):
            raise ValueError("SQL patient name must agree with selected DICOM study")
        try:
            rows, count = sql_gui_candidates.candidates(config, uid)
        except LookupError:
            abort(404)
        return jsonify(candidates=rows, total=count, read_only=True)

    @app.get('/api/study/<uid>/sql-candidate/<token>')
    def sql_candidate_text(uid, token):
        if not sql_review_permitted():
            return jsonify(error="SQL matching is temporarily disabled for patient-identity verification"), 503
        try:
            narrative = sql_gui_candidates.report_text(config, uid, token)
        except LookupError:
            abort(404)
        readable, format_status = format_report(narrative)
        return jsonify(text=narrative, formatted=readable,
                       format_status=format_status, read_only=True)

    @app.post('/api/study/<uid>/sql-association')
    def confirm_sql_association(uid):
        if not sql_review_permitted():
            return jsonify(error="SQL matching is temporarily disabled for patient-identity verification"), 503
        body = request.get_json(silent=True) or {}
        if body.get('confirm') is not True:
            raise ValueError('Explicit reviewer confirmation required')
        return jsonify(sql_association.confirm(config, uid, body.get('token', ''),
                         body.get('note', ''), body.get('date_verified'),
                         body.get('conflicts_acknowledged'), config['username']))

    @app.post('/api/study/<uid>/sql-draft')
    def prepare_sql_draft(uid):
        if not sql_review_permitted():
            return jsonify(error="SQL matching is temporarily disabled for patient-identity verification"), 503
        body = request.get_json(silent=True) or {}
        if body.get('confirm') is not True:
            raise ValueError('Explicit SQL draft preparation confirmation required')
        return jsonify(sql_prepare_review.prepare_draft(config, uid, config['username']))

    @app.get('/api/study/<uid>/sql-draft')
    def read_sql_draft(uid):
        if not sql_review_permitted():
            return jsonify(error="SQL matching is temporarily disabled for patient-identity verification"), 503
        return jsonify(sql_prepare_review.read_draft(config, uid))

    @app.get('/api/study/<uid>')
    def study_detail(uid):
        with database(config) as db:
            study = db.execute('SELECT * FROM studies WHERE uid=?', (uid,)).fetchone()
            if not study:
                abort(404)
            candidates = [dict(r) for r in db.execute('SELECT f.id,f.path,c.reason FROM candidates c JOIN files f ON f.id=c.report WHERE c.study=?', (uid,))]
            images = [dict(r) for r in db.execute("SELECT id,metadata FROM files WHERE kind='dicom' AND active=1 AND status='ok' AND json_extract(metadata,'$.StudyInstanceUID')=? ORDER BY id", (uid,))]
            report = db.execute('SELECT * FROM files WHERE id=?', (study['report_id'],)).fetchone() if study['report_id'] else None
            linked_studies = db.execute('SELECT count(*) FROM studies WHERE subject=?', (study['subject'],)).fetchone()[0]
            preparation = db.execute("SELECT id,state,message FROM jobs WHERE kind='prepare' AND json_extract(payload,'$.uid')=? ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
        if report and not any(r['id']==report['id'] for r in candidates):
            candidates.append({'id':report['id'],'path':'Associated '+report['kind']+' report','reason':'manual association'})
        try:
            text = engine.read_report(config, report)[0] if report else ''
        except ValueError:
            text=''
            study=dict(study)
            study['last_error']='Associated report unavailable or changed. Browse reports and associate a current record before preparation.'
        return jsonify(study=dict(study, linked_studies=linked_studies), candidates=candidates, images=images, original_report=text, preparation=dict(preparation) if preparation else None)

    @app.post('/api/jobs')
    def jobs():
        body = request.get_json()
        return jsonify(id=enqueue(config, body['kind'], body.get('payload'), config['username']))

    @app.post('/api/study/<uid>/approve')
    def approve(uid):
        body = request.get_json()
        if body.get('images_reviewed') is not True or body.get('report_reviewed') is not True:
            raise ValueError('Confirm review of all images/frames and the sanitized report')
        engine.approve(config, uid, body['text'], body.get('rectangles', []), body.get('note', ''), config['username'])
        return jsonify(ok=True)

    @app.get('/api/image/<int:file_id>')
    def image(file_id):
        with database(config) as db:
            row = db.execute("SELECT * FROM files WHERE id=? AND kind='dicom' AND active=1 AND status='ok'", (file_id,)).fetchone()
        if not row:
            abort(404)
        if row['size'] > config['max_dicom_bytes']:
            raise ValueError('Instance exceeds preview memory limit')
        ds = pydicom.dcmread(engine.source_path(config, row))
        frame = int(request.args.get('frame', 0))
        frames = int(ds.get('NumberOfFrames', 1))
        if frame < 0 or frame >= frames:
            raise ValueError('Frame out of range')
        from pydicom.pixels import pixel_array
        # Decode one requested frame rather than an entire multiframe object.
        pixels = pixel_array(ds, index=frame) if frames > 1 else ds.pixel_array
        if int(ds.get('SamplesPerPixel', 1)) != 1:
            raise ValueError('Color previews are unsupported; use a validated external viewer')
        pixels = pixels.astype(float) * float(ds.get('RescaleSlope', 1)) + float(ds.get('RescaleIntercept', 0))
        low, high = np.percentile(pixels, [1, 99])
        normalized = np.clip((pixels - low) / max(high - low, 1) * 255, 0, 255).astype('uint8')
        if ds.get('PhotometricInterpretation') == 'MONOCHROME1':
            normalized = 255 - normalized
        output = io.BytesIO()
        Image.fromarray(normalized).save(output, format='PNG')
        return Response(output.getvalue(), mimetype='image/png', headers={'X-Frames': str(frames), 'X-Rows': str(ds.Rows), 'X-Columns': str(ds.Columns)})

    return app
