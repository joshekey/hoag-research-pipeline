"""Authenticated internal workflow UI; no third-party scripts or data services."""
import io
import json
import secrets
from functools import wraps
from pathlib import Path

import numpy as np
import pydicom
from flask import Flask, Response, abort, jsonify, render_template, request, session
from PIL import Image
from werkzeug.security import check_password_hash

import engine
from store import database, enqueue, initialize, token


def create_app(config):
    initialize(config)
    app = Flask(__name__)
    app.secret_key = config['secret_key']
    app.config.update(MAX_CONTENT_LENGTH=12 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Strict', SESSION_COOKIE_SECURE=config.get('secure_cookies', True))

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
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' blob:; frame-ancestors 'none'"
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

    @app.get('/api/status')
    def status():
        with database(config) as db:
            counts = [dict(r) for r in db.execute('SELECT kind,status,count(*) count,sum(size) bytes FROM files WHERE active=1 GROUP BY kind,status')]
            states = [dict(r) for r in db.execute('SELECT state,count(*) count FROM studies GROUP BY state')]
            jobs = [dict(r) for r in db.execute('SELECT id,kind,state,progress,message,created FROM jobs ORDER BY id DESC LIMIT 20')]
            sql = [dict(r) for r in db.execute("SELECT id,metadata FROM files WHERE kind='sql' AND active=1")]
            exports = [dict(r) for r in db.execute('SELECT id,folder,created FROM exports ORDER BY id DESC LIMIT 20')]
        return jsonify(counts=counts, states=states, jobs=jobs, sql=sql, exports=exports,
                       sources=config['source_roots'], output=config['output_root'])

    @app.get('/api/studies')
    def studies():
        q = '%' + request.args.get('q', '')[:200] + '%'
        offset = max(0, int(request.args.get('offset', 0)))
        with database(config) as db:
            rows = [dict(r) for r in db.execute('''SELECT uid,subject,accession,patient,name,date,modality,count,state
                FROM studies WHERE accession LIKE ? OR patient LIKE ? OR name LIKE ? OR subject LIKE ?
                ORDER BY date DESC,uid LIMIT 100 OFFSET ?''', (q, q, q, q, offset))]
        return jsonify(rows)

    @app.get('/api/reports')
    def reports():
        q = '%' + request.args.get('q', '')[:200] + '%'
        with database(config) as db:
            rows = [dict(r) for r in db.execute("SELECT id,path FROM files WHERE kind='report' AND active=1 AND status='ok' AND path LIKE ? LIMIT 100", (q,))]
        return jsonify(rows)

    @app.get('/api/study/<uid>')
    def study_detail(uid):
        with database(config) as db:
            study = db.execute('SELECT * FROM studies WHERE uid=?', (uid,)).fetchone()
            if not study:
                abort(404)
            candidates = [dict(r) for r in db.execute('SELECT f.id,f.path,c.reason FROM candidates c JOIN files f ON f.id=c.report WHERE c.study=?', (uid,))]
            images = [dict(r) for r in db.execute("SELECT id,metadata FROM files WHERE kind='dicom' AND active=1 AND status='ok' AND json_extract(metadata,'$.StudyInstanceUID')=? ORDER BY id", (uid,))]
            report = db.execute('SELECT * FROM files WHERE id=?', (study['report_id'],)).fetchone() if study['report_id'] else None
        text = engine.read_report(config, report)[0] if report else ''
        return jsonify(study=dict(study), candidates=candidates, images=images, original_report=text)

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
