"""Source discovery, conservative matching, reviewed de-identification and exports."""
import csv
import hashlib
import io
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

import pydicom
from pydicom.errors import InvalidDicomError
from store import audit, database, idle, progress, research_uid, token

TAGS = ['StudyInstanceUID', 'SeriesInstanceUID', 'SOPInstanceUID', 'SOPClassUID',
        'AccessionNumber', 'PatientID', 'IssuerOfPatientID', 'PatientName',
        'StudyDate', 'Modality', 'BurnedInAnnotation']


def checked_root(config, path, output=False):
    root = Path(path).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Configured root is not a directory')
    if config.get('require_mounts', True) and not os.path.ismount(root):
        raise ValueError('Configured share is not mounted; refusing to use the local mountpoint')
    if config.get('require_mounts', True) and hasattr(os, 'statvfs'):
        readonly = bool(os.statvfs(root).f_flag & os.ST_RDONLY)
        if not output and not readonly:
            raise ValueError('Source share is not mounted read-only')
        if output and readonly:
            raise ValueError('Output share is mounted read-only')
    if output:
        for source in config['source_roots']:
            src = Path(source).resolve()
            if root == src or root.is_relative_to(src) or src.is_relative_to(root):
                raise ValueError('Output and source roots must not overlap')
        if shutil.disk_usage(root).free < config['minimum_free_bytes']:
            raise ValueError('Output share does not have the configured minimum free space')
    return root


def source_path(config, row):
    root = checked_root(config, row['root'])
    path = Path(row['path'])
    if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
        raise ValueError('Source path escaped its configured root')
    stat = path.stat()
    if (stat.st_size, stat.st_mtime_ns) != (row['size'], row['mtime']):
        raise ValueError('Source changed; rescan and review again')
    return path


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def file_digest(path):
    hasher = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def read_report(config, row):
    path = source_path(config, row)
    if path.stat().st_size > config['max_report_bytes']:
        raise ValueError('Report exceeds configured size limit')
    raw = path.read_bytes()
    source_path(config, row)
    text = raw.decode(config['report_encoding'], errors='strict')
    if '\x00' in text:
        raise ValueError('Report contains binary data; check configured encoding')
    return text, digest(raw)


def selected_path(root, relative):
    if not isinstance(relative, str) or '\\' in relative or relative.startswith('/') or any(part in ('..', '') for part in relative.split('/')):
        raise ValueError('Invalid relative folder path')
    path = root
    for part in relative.split('/'):
        path = path / part
        if path.is_symlink():
            raise ValueError('Symlink folders cannot be selected')
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_dir():
        raise ValueError('Folder is outside the configured source or is not a directory')
    return resolved


def selection(config, folders=None):
    roots = [checked_root(config, r) for r in config['source_roots']]
    for i, root in enumerate(roots):
        if any(root == other or root.is_relative_to(other) or other.is_relative_to(root) for other in roots[:i]):
            raise ValueError('Source roots overlap')
    if folders is None:
        return [(root, root) for root in roots]
    if not isinstance(folders, list) or not 1 <= len(folders) <= 1000:
        raise ValueError('Select between 1 and 1000 folders')
    chosen = []
    for folder in folders:
        if not isinstance(folder, dict) or type(folder.get('root')) is not int or not 0 <= folder['root'] < len(roots):
            raise ValueError('Invalid source share selection')
        root = roots[folder['root']]
        path = selected_path(root, folder.get('relative', '.'))
        if any(path == other or path.is_relative_to(other) for _, other in chosen):
            continue
        chosen = [(r, p) for r, p in chosen if not p.is_relative_to(path)]
        chosen.append((root, path))
    return chosen


def browse_folders(config, root_index, relative='.', offset=0):
    if not 0 <= root_index < len(config['source_roots']) or offset < 0:
        raise ValueError('Invalid source share or offset')
    root = checked_root(config, config['source_roots'][root_index])
    path = selected_path(root, relative)
    children = sorted((p for p in path.iterdir() if not p.is_symlink() and p.is_dir()), key=lambda p: p.name.casefold())
    return {'root': root_index, 'relative': relative, 'total': len(children),
            'children': [{'name': p.name, 'relative': p.relative_to(root).as_posix()} for p in children[offset:offset + 200]]}


def scan(config, job=None, folders=None):
    with database(config) as db:
        db.execute("UPDATE settings SET value='0' WHERE key='scan_complete'")
        db.execute("UPDATE studies SET state='needs_review', approved_fingerprint=NULL, approved_hash=NULL")
    scopes = selection(config, folders)
    run = str(uuid.uuid4())
    count = 0
    for root, selected in scopes:
        def onerror(error):
            raise OSError('Source traversal failed') from None
        for directory, dirs, names in os.walk(selected, onerror=onerror, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
            for name in names:
                path = Path(directory) / name
                if path.is_symlink():
                    continue
                stat = path.stat()
                with database(config) as db:
                    old = db.execute('SELECT * FROM files WHERE path=?', (str(path),)).fetchone()
                    if old and old['status'] in ('ok', 'ignored') and (old['size'], old['mtime']) == (stat.st_size, stat.st_mtime_ns):
                        db.execute('UPDATE files SET active=1,seen=? WHERE id=?', (run, old['id']))
                        count += 1
                        continue
                kind, status, meta = 'other', 'ignored', {}
                try:
                    if path.suffix.lower() == '.txt':
                        kind = 'report'
                        if stat.st_size > config['max_report_bytes']:
                            raise ValueError('Oversized report')
                        raw = path.read_bytes()
                        text = raw.decode(config['report_encoding'], errors='strict')
                        if '\x00' in text:
                            raise ValueError('Binary report')
                        # Only explicit labels and exact filename stems become matching keys.
                        keys = re.findall(r'(?im)^\s*(?:accession(?:\s*(?:number|no\.?|#))?|acc(?:\s*#)?)\s*[:=]\s*([A-Za-z0-9._-]+)', text)
                        uids = re.findall(r'(?im)^\s*Study(?:\s*Instance)?\s*UID\s*[:=]\s*([0-9.]+)', text)
                        meta = {'hash': digest(raw), 'accessions': list(set(keys + [path.stem])), 'uids': uids}
                        status = 'ok'
                    elif path.suffix.lower() == '.sql':
                        kind = 'sql'
                        with path.open('rb') as f:
                            header = f.read(65536).decode('utf-8', errors='replace')
                        meta = {'possible_type': next((label for label, pattern in [
                            ('PostgreSQL', r'PostgreSQL database dump|^PGDMP'),
                            ('MySQL/MariaDB', r'MySQL dump|MariaDB'),
                            ('SQL Server', r'\[dbo\]|^GO\s*$'),
                            ('SQLite', r'SQLite format|PRAGMA foreign_keys')]
                            if re.search(pattern, header, re.M | re.I)), 'Unknown')}
                        status = 'ok'
                    else:
                        ds = pydicom.dcmread(path, stop_before_pixels=True, specific_tags=TAGS)
                        kind = 'dicom'
                        if not all(ds.get(tag) for tag in ('StudyInstanceUID', 'SeriesInstanceUID', 'SOPInstanceUID')):
                            raise ValueError('Missing DICOM identifiers')
                        meta = {tag: str(ds.get(tag, '')) for tag in TAGS}
                        status = 'ok'
                    after = path.stat()
                    if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                        raise ValueError('Source changed')
                except InvalidDicomError:
                    # A .dcm with an invalid header is actionable; unrelated files are ignored.
                    if path.suffix.lower() == '.dcm':
                        kind, status = 'dicom', 'error'
                except (ValueError, OSError, EOFError):
                    status, meta = 'error', {}
                with database(config) as db:
                    db.execute('''INSERT INTO files(path,root,kind,size,mtime,metadata,status,seen)
                        VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET
                        root=excluded.root,kind=excluded.kind,size=excluded.size,mtime=excluded.mtime,
                        metadata=excluded.metadata,status=excluded.status,seen=excluded.seen,active=1''',
                        (str(path), str(root), kind, stat.st_size, stat.st_mtime_ns, json.dumps(meta), status, run))
                count += 1
                if count % 100 == 0:
                    progress(config, job, count, 'Indexing source files')
    with database(config) as db:
        db.execute('UPDATE files SET active=0 WHERE seen IS NULL OR seen<>?', (run,))
        db.execute("INSERT OR REPLACE INTO settings VALUES ('scan_folders',?)", (json.dumps(folders),))
    match(config, job, from_scan=True)
    with database(config) as db:
        db.execute("UPDATE settings SET value='1' WHERE key='scan_complete'")
    progress(config, job, count, 'Scan and candidate matching completed')


def require_scan(db):
    if db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()[0] != '1':
        raise ValueError('A complete successful source scan is required before matching, review, or export')


def inspect_sql(config, file_id, job=None):
    from sql_schema import inspect
    with database(config) as db:
        row = db.execute("SELECT * FROM files WHERE id=? AND kind='sql' AND active=1 AND status='ok'", (file_id,)).fetchone()
    if not row:
        raise ValueError('SQL dump is not in the current selected inventory')
    path = source_path(config, row)
    result = inspect(path)
    source_path(config, row)
    meta = json.loads(row['metadata'])
    meta['schema'] = result
    with database(config) as db:
        db.execute('UPDATE files SET metadata=? WHERE id=?', (json.dumps(meta), file_id))
        audit(db, 'worker', 'inspect-sql-schema', str(file_id))
    progress(config, job, len(result['tables']), 'SQL schema inspected without executing SQL')


def match(config, job=None, from_scan=False):
    with database(config) as db:
        if not from_scan:
            require_scan(db)
        db.execute("UPDATE studies SET state='needs_review',approved_fingerprint=NULL,approved_hash=NULL")
        db.execute('DELETE FROM report_keys')
        for row in db.execute("SELECT * FROM files WHERE kind='report' AND active=1 AND status='ok'"):
            meta = json.loads(row['metadata'])
            for key in meta['accessions']:
                db.execute('INSERT OR IGNORE INTO report_keys VALUES (?,?,?)', (key, row['id'], 'Accession / filename'))
            for key in meta['uids']:
                db.execute('INSERT OR IGNORE INTO report_keys VALUES (?,?,?)', (key, row['id'], 'Study UID'))
        db.execute('DELETE FROM candidates')
        count = 0
        for group in db.execute("SELECT DISTINCT json_extract(metadata,'$.StudyInstanceUID') uid FROM files WHERE kind='dicom' AND active=1 AND status='ok'"):
            uid = group['uid']
            rows = [dict(r) for r in db.execute("SELECT * FROM files WHERE kind='dicom' AND active=1 AND status='ok' AND json_extract(metadata,'$.StudyInstanceUID')=?", (uid,))]
            metas = [json.loads(row['metadata']) for row in rows]
            first = metas[0]
            identities = {(m['PatientID'], m['IssuerOfPatientID'], m['PatientName']) for m in metas}
            accessions = {m['AccessionNumber'] for m in metas if m['AccessionNumber']}
            patient_key = first['IssuerOfPatientID'] + '\0' + first['PatientID'] if first['PatientID'] else uid
            subject = 'SUBJECT_' + token(config, 'subject', patient_key)[:20]
            fingerprint = digest(json.dumps(sorted((r['id'], r['size'], r['mtime'], r['metadata']) for r in rows)).encode())
            state = 'conflict' if len(identities) > 1 or len(accessions) > 1 else 'unmatched'
            db.execute('''INSERT INTO studies(uid,subject,accession,patient,name,date,modality,count,size,fingerprint,state)
                VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET
                subject=excluded.subject,accession=excluded.accession,patient=excluded.patient,
                name=excluded.name,date=excluded.date,modality=excluded.modality,count=excluded.count,
                size=excluded.size,fingerprint=excluded.fingerprint,state=excluded.state,
                report_id=NULL,report_hash=NULL,sanitized=NULL,approved_fingerprint=NULL,approved_hash=NULL''',
                (uid, subject, first['AccessionNumber'], first['PatientID'], first['PatientName'], first['StudyDate'],
                 ','.join(sorted({m['Modality'] for m in metas})), len(rows), sum(r['size'] for r in rows), fingerprint, state))
            candidates = db.execute("SELECT report,group_concat(DISTINCT reason) reason FROM report_keys WHERE (key=? AND reason='Study UID') OR (key=? AND reason='Accession / filename' AND key<>'') GROUP BY report", (uid, first['AccessionNumber'])).fetchall()
            for candidate in candidates:
                db.execute('INSERT INTO candidates VALUES (?,?,?)', (uid, candidate['report'], candidate['reason']))
            if candidates and state != 'conflict':
                db.execute('UPDATE studies SET state=? WHERE uid=?', ('candidate' if len(candidates) == 1 else 'ambiguous', uid))
            count += 1
        db.execute("DELETE FROM studies WHERE uid NOT IN (SELECT json_extract(metadata,'$.StudyInstanceUID') FROM files WHERE kind='dicom' AND active=1 AND status='ok')")
    progress(config, job, count, 'Candidate matches refreshed; select and review reports')


_analyzer = None


def analyzer():
    global _analyzer
    if _analyzer is None:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        # Never auto-download a model at runtime.
        import spacy
        if not spacy.util.is_package('en_core_web_sm'):
            raise RuntimeError('NLP model not installed')
        provider = NlpEngineProvider(nlp_configuration={'nlp_engine_name': 'spacy',
            'models': [{'lang_code': 'en', 'model_name': 'en_core_web_sm'}]})
        _analyzer = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=['en'])
    return _analyzer


def sanitize_report(text, identifiers, nlp=None):
    spans = [(r.start, r.end) for r in (nlp or analyzer()).analyze(text=text, language='en', score_threshold=0.35)]
    for value in identifiers:
        for part in {value, *re.split(r'[\^=]', value)}:
            if part and len(part) >= 2:
                spans.extend((m.start(), m.end()) for m in re.finditer(r'(?<!\w)' + re.escape(part) + r'(?!\w)', text, re.I))
    # Explicit hospital identifiers and dates complement NLP; review remains mandatory.
    for pattern in [r'(?im)^\s*(?:patient|name|mrn|medical record|accession|dob|date of birth|address|phone|email|physician|referring|dictated by|signed by)\b[^\n]*',
                    r'\b\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b', r'\b\d{8}\b',
                    r'\b\d{3}[-. ]\d{2}[-. ]\d{4}\b']:
        spans.extend((m.start(), m.end()) for m in re.finditer(pattern, text))
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    for start, end in reversed(merged):
        text = text[:start] + '[REDACTED]' + text[end:]
    return text


def prepare(config, uid, report_id, job=None, nlp=None):
    with database(config) as db:
        require_scan(db)
        study = db.execute('SELECT * FROM studies WHERE uid=?', (uid,)).fetchone()
        report = db.execute("SELECT * FROM files WHERE id=? AND kind='report' AND active=1 AND status='ok'", (report_id,)).fetchone()
        if not study or not report or study['state'] == 'conflict':
            raise ValueError('Study or report unavailable / conflicting study identity')
        instances = db.execute("SELECT * FROM files WHERE kind='dicom' AND active=1 AND status='ok' AND json_extract(metadata,'$.StudyInstanceUID')=?", (uid,)).fetchall()
        identifiers = set()
        for row in instances:
            meta = json.loads(row['metadata'])
            identifiers.update(meta.get(k, '') for k in ('PatientID', 'PatientName', 'AccessionNumber', 'StudyInstanceUID', 'SeriesInstanceUID', 'SOPInstanceUID'))
    text, report_hash = read_report(config, report)
    if report_hash != json.loads(report['metadata'])['hash']:
        raise ValueError('Report content changed; rescan')
    clean = sanitize_report(text, identifiers, nlp)
    hashes = {}
    for count, row in enumerate(instances, 1):
        hashes[str(row['id'])] = file_digest(source_path(config, row))
        source_path(config, row)
        if count % 20 == 0:
            progress(config, job, count, 'Fingerprinting images for review')
    with database(config) as db:
        db.execute("UPDATE studies SET report_id=?,report_hash=?,sanitized=?,source_hashes=?,redactions='[]',state='review',last_error='',approved_hash=NULL,approved_fingerprint=NULL WHERE uid=?",
                   (report_id, report_hash, clean, json.dumps(hashes, sort_keys=True), uid))
        audit(db, 'worker', 'report-prepared', token(config, 'study', uid)[:20])
    progress(config, job, 1, 'Report ready for review; inspect images and report before approval')


def approve(config, uid, clean, rectangles, note, actor='admin'):
    if not clean.strip() or len(clean.encode()) > config['max_report_bytes'] or not note.strip():
        raise ValueError('A sanitized report and review note are required')
    if not isinstance(rectangles, list) or len(rectangles) > 100:
        raise ValueError('Pixel masks must be a list of up to 100 rectangles')
    for rect in rectangles:
        if not isinstance(rect, list) or len(rect) != 4 or any(type(v) is not int or v < 0 for v in rect) or rect[2] <= 0 or rect[3] <= 0:
            raise ValueError('Each mask is [x, y, width, height] with nonnegative origin and positive size')
    with database(config) as db:
        db.execute('BEGIN IMMEDIATE')
        idle(db)
        require_scan(db)
        study = db.execute('SELECT * FROM studies WHERE uid=?', (uid,)).fetchone()
        if not study or study['state'] != 'review':
            raise ValueError('Prepare a report before approving')
        approval_hash = digest(json.dumps([clean, rectangles, note, study['report_id'], study['report_hash'], study['source_hashes']], sort_keys=True).encode())
        db.execute("UPDATE studies SET sanitized=?,redactions=?,review_note=?,approved_hash=?,approved_fingerprint=fingerprint,state='approved' WHERE uid=?",
                   (clean, json.dumps(rectangles), note, approval_hash, uid))
        audit(db, actor, 'approve-report-and-images', token(config, 'study', uid)[:20])


def mask_pixels(ds, rectangles):
    if not rectangles:
        return
    import numpy as np
    from pydicom.uid import ExplicitVRLittleEndian
    pixels = ds.pixel_array.copy()
    h, w = int(ds.Rows), int(ds.Columns)
    if int(ds.get('SamplesPerPixel', 1)) != 1:
        raise ValueError('Color pixel masking is unsupported; quarantine this study')
    for x, y, width, height in rectangles:
        if x + width > w or y + height > h:
            raise ValueError('Pixel mask is outside an image; define masks appropriate for every instance')
        pixels[..., y:y + height, x:x + width] = 0
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.PixelData = pixels.astype(pixels.dtype.newbyteorder('<')).tobytes()
    ds['PixelData'].is_undefined_length = False
    ds['PixelData'].VR = 'OB' if ds.BitsAllocated <= 8 else 'OW'
    ds.BurnedInAnnotation = 'NO'


def anonymize_dataset(config, ds, subject, rectangles):
    from dicomanonymizer import anonymize_dataset as standard_anonymize
    from dicomanonymizer.simpledicomanonymizer import initialize_actions
    original = str(ds.SOPInstanceUID)
    # Restrict to conventional image objects; SR/PDF/PR/RT require a dedicated profile.
    allowed = {pydicom.uid.CTImageStorage, pydicom.uid.MRImageStorage,
               pydicom.uid.ComputedRadiographyImageStorage, pydicom.uid.DigitalXRayImageStorageForPresentation,
               pydicom.uid.DigitalMammographyXRayImageStorageForPresentation,
               pydicom.uid.UltrasoundImageStorage, pydicom.uid.UltrasoundMultiFrameImageStorage,
               pydicom.uid.SecondaryCaptureImageStorage, pydicom.uid.EnhancedCTImageStorage,
               pydicom.uid.EnhancedMRImageStorage}
    if str(ds.SOPClassUID) not in allowed or 'PixelData' not in ds:
        raise ValueError('Unsupported DICOM SOP class; study quarantined')
    if str(ds.get('BurnedInAnnotation', '')).upper() == 'YES' and not rectangles:
        raise ValueError('Burned-in annotations require configured pixel masks')
    mask_pixels(ds, rectangles)
    # Capture non-class UIDs, including nested references, before baseline actions.
    remapped = {}
    def capture(dataset):
        for element in dataset:
            if element.VR == 'SQ':
                for item in element.value:
                    capture(item)
            elif element.VR == 'UI' and 'ClassUID' not in element.keyword and element.keyword != 'TransferSyntaxUID':
                values = list(element.value) if element.VM > 1 else [element.value]
                remapped[id(element)] = (element, [research_uid(config, str(v)) for v in values])
    capture(ds)
    # Preserve captured UID values until final recursive cleanup; apply each mapping once.
    def keep_uid(dataset, tag):
        pass
    extra = {}
    for element in ds.iterall():
        if element.VR == 'UI' and 'ClassUID' not in element.keyword and element.keyword != 'TransferSyntaxUID':
            extra[(element.tag.group, element.tag.element)] = keep_uid
    def baseline(dataset):
        for element in list(dataset):
            if element.VR == 'SQ':
                for item in element.value:
                    baseline(item)
        standard_anonymize(dataset, extra_anonymization_rules=extra,
                          base_rules_gen=lambda: initialize_actions('dicomfields_2026c'))
    baseline(ds)
    ds.remove_private_tags()
    # Conservative extra cleanup: remove free text/descriptors and dates at all levels.
    def clean(dataset):
        for element in list(dataset):
            if element.tag.group in (0x0040, 0x0088) or 0x6000 <= element.tag.group <= 0x60FF:
                del dataset[element.tag]
            elif element.VR == 'SQ':
                for item in element.value:
                    clean(item)
            elif element.VR == 'UI' and id(element) in remapped and remapped[id(element)][0] is element:
                values = remapped[id(element)][1]
                element.value = values if len(values) > 1 else values[0]
            elif element.VR in ('PN', 'DA', 'DT', 'TM', 'LT', 'ST', 'UT', 'UC', 'AE', 'UR'):
                element.value = ''
            elif element.VR in ('LO', 'SH') and element.keyword not in ('PhotometricInterpretation',):
                element.value = ''
    clean(ds)
    ds.PatientID = subject
    ds.PatientName = subject
    ds.PatientIdentityRemoved = 'YES'
    ds.DeidentificationMethod = 'Kitware 2026c + local cleanup + reviewed images/report'
    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
    ds.file_meta.ImplementationClassUID = '2.25.319259993028359174956403443493574400001'
    for tag in ('SourceApplicationEntityTitle', 'SendingApplicationEntityTitle', 'ReceivingApplicationEntityTitle', 'PrivateInformation', 'PrivateInformationCreatorUID'):
        if tag in ds.file_meta:
            del ds.file_meta[tag]
    ds.preamble = b'\0' * 128
    if str(ds.SOPInstanceUID) != research_uid(config, original):
        raise ValueError('UID mapping validation failed')
    return ds


def export_study(config, uid, job=None):
    output = checked_root(config, config['output_root'], output=True)
    with database(config) as db:
        require_scan(db)
        study = db.execute('SELECT * FROM studies WHERE uid=?', (uid,)).fetchone()
        if not study or study['state'] != 'approved' or study['fingerprint'] != study['approved_fingerprint']:
            raise ValueError('Study has not passed report and image review')
        report = db.execute('SELECT * FROM files WHERE id=? AND active=1', (study['report_id'],)).fetchone()
        rows = db.execute("SELECT * FROM files WHERE kind='dicom' AND active=1 AND status='ok' AND json_extract(metadata,'$.StudyInstanceUID')=? ORDER BY id", (uid,)).fetchall()
    if not report or len(rows) != study['count']:
        raise ValueError('Catalog changed; rescan and review')
    current_approval = digest(json.dumps([study['sanitized'], json.loads(study['redactions']), study['review_note'], study['report_id'], study['report_hash'], study['source_hashes']], sort_keys=True).encode())
    if current_approval != study['approved_hash']:
        raise ValueError('Review content changed; approve again')
    _, report_hash = read_report(config, report)
    if report_hash != study['report_hash']:
        raise ValueError('Report changed; rescan and review')
    if shutil.disk_usage(output).free < study['size'] * 3 + config['minimum_free_bytes']:
        raise ValueError('Insufficient free output space for staging and transformed images')
    rectangles = json.loads(study['redactions'])
    staging = output / '.staging'
    staging.mkdir(mode=0o700, exist_ok=True)
    if staging.is_symlink():
        raise ValueError('Staging directory must not be a symlink')
    bundle = staging / str(uuid.uuid4())
    bundle.mkdir(mode=0o700)
    entries, seen = [], {}
    try:
        (bundle / 'DICOM').mkdir()
        (bundle / 'Report').mkdir()
        for count, row in enumerate(rows, 1):
            path = source_path(config, row)
            if row['size'] > config['max_dicom_bytes']:
                raise ValueError('DICOM exceeds per-instance memory limit')
            raw = path.read_bytes()
            source_path(config, row)
            ds = pydicom.dcmread(io.BytesIO(raw))
            meta = json.loads(row['metadata'])
            if any(str(ds.get(k, '')) != meta[k] for k in TAGS):
                raise ValueError('DICOM metadata differs from reviewed catalog')
            original_uid = str(ds.SOPInstanceUID)
            raw_hash = digest(raw)
            if raw_hash != json.loads(study['source_hashes']).get(str(row['id'])):
                raise ValueError('Source content differs from reviewed fingerprint; prepare and review again')
            if original_uid in seen:
                if seen[original_uid] != raw_hash:
                    raise ValueError('Conflicting files have the same SOP Instance UID')
                continue
            seen[original_uid] = raw_hash
            del raw
            ds = anonymize_dataset(config, ds, study['subject'], rectangles)
            target = bundle / 'DICOM' / (str(ds.SOPInstanceUID) + '.dcm')
            ds.save_as(target, enforce_file_format=True)
            verified = pydicom.dcmread(target, stop_before_pixels=True)
            if str(verified.StudyInstanceUID) != research_uid(config, uid) or verified.PatientID != study['subject']:
                raise ValueError('Output validation failed')
            entries.append({'path': 'DICOM/' + target.name, 'sha256': file_digest(target), 'bytes': target.stat().st_size})
            progress(config, job, count, 'Writing reviewed research package')
        report_path = bundle / 'Report' / 'report.txt'
        report_path.write_text(study['sanitized'], encoding='utf-8')
        entries.append({'path': 'Report/report.txt', 'sha256': digest(report_path.read_bytes()), 'bytes': report_path.stat().st_size})
        manifest = {'version': 1, 'subject_id': study['subject'], 'study_id': research_uid(config, uid),
                    'profile': 'Kitware baseline + conservative cleanup + human review', 'files': entries}
        manifest_path = bundle / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        with (bundle / 'manifest.csv').open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['path', 'sha256', 'bytes'])
            writer.writeheader()
            writer.writerows(entries)
        # Exclusive final path; never overwrite an earlier package.
        subject_dir = output / study['subject']
        subject_dir.mkdir(exist_ok=True)
        if subject_dir.is_symlink():
            raise ValueError('Subject directory must not be a symlink')
        final = subject_dir / ('STUDY_' + token(config, 'study', uid)[:20] + '_' + uuid.uuid4().hex[:8])
        bundle.rename(final)
        with database(config) as db:
            db.execute('INSERT INTO exports(study,folder,manifest_hash,created) VALUES (?,?,?,?)',
                       (uid, str(final), digest((final / 'manifest.json').read_bytes()), time.time()))
            db.execute("UPDATE studies SET state='exported' WHERE uid=?", (uid,))
            audit(db, 'worker', 'export-completed', final.name)
        return final
    except BaseException:
        # Partial files stay in a restricted staging directory for operator recovery.
        with database(config) as db:
            db.execute("UPDATE studies SET state='review',approved_hash=NULL,approved_fingerprint=NULL WHERE uid=?", (uid,))
        raise
