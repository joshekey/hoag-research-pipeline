"""Study-scoped TXT discovery and explicit association. No automatic approval."""
import json
from pathlib import Path
import pydicom
import engine
from store import audit, database, idle
from strict_identity_match import normalize_name, normalize_date
from txt_candidate_discovery import clues, all_patient_names


def trusted_study(config, uid):
    with database(config) as db:
        engine.require_scan(db)
        study = db.execute('SELECT * FROM studies WHERE uid=?', (uid,)).fetchone()
        first = db.execute("SELECT * FROM files WHERE kind='dicom' AND active=1 AND status='ok' AND json_extract(metadata,'$.StudyInstanceUID')=? ORDER BY id LIMIT 1", (uid,)).fetchone()
    if not study or not first or study['state'] == 'conflict':
        raise ValueError('Selected study is unavailable or has conflicting identity')
    ds = pydicom.dcmread(engine.source_path(config, first), stop_before_pixels=True,
                        specific_tags=['PatientName','PatientID','PatientBirthDate','StudyInstanceUID','StudyDate'])
    if str(ds.get('StudyInstanceUID','')) != uid or normalize_name(ds.get('PatientName','')) != normalize_name(study['name']):
        raise ValueError('DICOM source identity differs from the selected study')
    return dict(study), ds


def evidence(study, ds, text, filename):
    info = clues(text, Path(filename).stem)
    names = all_patient_names(text)
    conflicts = []
    for label, actual, expected in [
        ('patient name', info['name'], normalize_name(study['name'])),
        ('patient ID', info['patient_id'], info_id(ds.get('PatientID',''))),
        ('date of birth', info['dob'], normalize_date(ds.get('PatientBirthDate',''))),
        ('study UID', info['study_uid'], study['uid']),
        ('accession', info['accession'], info_id(study['accession'])),
        ('exam date', info['exam_date'], normalize_date(study['date']))]:
        if actual and expected and actual != expected:
            conflicts.append(label)
    if info['name_conflict'] or (names and names != {normalize_name(study['name'])}):
        conflicts.append('patient name declarations')
    return info, sorted(set(conflicts))


def info_id(value):
    from txt_candidate_discovery import clean_id
    return clean_id(value)


def candidates(config, uid, query=None, sort='reason', offset=0):
    study, ds = trusted_study(config, uid)
    surname = engine.family_name(str(ds.get('PatientName','')))
    automatic = query is None
    query = surname if query is None else query.strip()[:120]
    order = {'reason': 'CASE WHEN c.reason LIKE "%Study UID%" THEN 0 WHEN c.reason LIKE "%Accession%" THEN 1 ELSE 2 END,f.path',
             'filename': 'f.path', 'modified': 'f.mtime DESC,f.path',
             'patient': "json_extract(f.metadata,'$.name_hint'),f.path"}.get(sort)
    if not order or offset < 0:
        raise ValueError('Invalid report sorting or page')
    where = "f.kind='report' AND f.active=1 AND f.status='ok'"
    params = [uid]
    if query:
        where += (' AND (c.report IS NOT NULL OR f.id IN (SELECT rowid FROM report_search WHERE report_search MATCH ?))' if automatic
                  else ' AND f.id IN (SELECT rowid FROM report_search WHERE report_search MATCH ?)')
        params.append(engine.fts_phrase(query))
    elif automatic:
        where += ' AND c.report IS NOT NULL'
    with database(config) as db:
        parser = db.execute("SELECT value FROM settings WHERE key='scan_parser'").fetchone()
        if not parser or parser[0] != '3:' + config['report_encoding']:
            raise ValueError('TXT surname index is not ready. Rescan the selected DICOM and report folders.')
        sql = ' FROM files f LEFT JOIN candidates c ON c.report=f.id AND c.study=? WHERE ' + where
        total = db.execute('SELECT count(*)' + sql, params).fetchone()[0]
        rows = [dict(r) for r in db.execute('SELECT f.id,f.path,f.metadata,f.mtime,c.reason' + sql + ' ORDER BY ' + order + ' LIMIT 100 OFFSET ?', [*params,offset])]
    result = []
    for row in rows:
        meta = json.loads(row.pop('metadata'))
        row.update(source='TXT', name=meta.get('name_hint',''), reason=row['reason'] or 'Last name search; unverified suggestion')
        result.append(row)
    return {'rows': result, 'total': total, 'offset': offset, 'surname': surname,
            'source_status': 'searched', 'sql_status': 'independent safety gate', 'manual_confirmation_required': True}


def preview(config, uid, report_id):
    study, ds = trusted_study(config, uid)
    with database(config) as db:
        row = db.execute("SELECT * FROM files WHERE id=? AND kind='report' AND active=1 AND status='ok'", (report_id,)).fetchone()
    if not row:
        raise ValueError('TXT report is not in the active inventory')
    text, sha = engine.read_report(config, row)
    if sha != json.loads(row['metadata']).get('hash'):
        raise ValueError('Report changed; rescan before association')
    info, conflicts = evidence(study, ds, text, row['path'])
    # Return evidence for conflicts, never the other patient's narrative.
    return {'uid': uid, 'id': report_id, 'text': '' if conflicts else text,
            'conflicts': conflicts, 'preview_allowed': not conflicts,
            'name': info['name'], 'exam_date': info['exam_date'],
            'patient_id_present': bool(info['patient_id']), 'sha256': sha,
            'message': 'Conflicting report identity/examination; association blocked' if conflicts else 'Unverified candidate: confirm patient and examination before association'}


def associate(config, uid, report_id, note, confirmed, actor):
    if confirmed is not True or not isinstance(note,str) or not 15 <= len(note.strip()) <= 2000:
        raise ValueError('Confirm patient and examination, and enter a review note of 15–2000 characters')
    previewed = preview(config, uid, report_id)
    if not previewed['preview_allowed']:
        raise ValueError('Conflicting report identity/examination; association blocked')
    with database(config) as db:
        db.execute('BEGIN IMMEDIATE'); idle(db)
        engine.require_scan(db)
        fingerprint = db.execute('SELECT fingerprint FROM studies WHERE uid=?',(uid,)).fetchone()[0]
        if db.execute('''SELECT 1 FROM txt_associations a JOIN studies s ON s.uid=a.uid
                         WHERE a.report_id=? AND a.uid<>? AND a.fingerprint=s.fingerprint
                         AND a.report_hash=? LIMIT 1''',(report_id,uid,previewed['sha256'])).fetchone():
            raise ValueError('This report is already associated with another study; investigate the examination relationship')
        db.execute('''INSERT OR REPLACE INTO txt_associations(uid,report_id,fingerprint,report_hash,note)
                      VALUES (?,?,?,?,?)''',(uid,report_id,fingerprint,previewed['sha256'],note.strip()))
        db.execute("UPDATE studies SET report_id=?,sanitized=NULL,report_hash=NULL,state='needs_review',approved_hash=NULL,approved_fingerprint=NULL WHERE uid=?",(report_id,uid))
        audit(db,actor,'associate-txt-report',uid)
    return {'associated':True,'approved':False,'uid':uid,'report_id':report_id}
