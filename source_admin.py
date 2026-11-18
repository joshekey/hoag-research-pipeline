"""Read-only source coverage and bounded DICOM description summaries."""
import json

from store import database


def study_description(db, uid):
    rows = db.execute("""SELECT DISTINCT trim(json_extract(metadata,'$.StudyDescription')) description
        FROM files WHERE kind='dicom' AND active=1 AND status='ok'
        AND json_extract(metadata,'$.StudyInstanceUID')=?
        AND trim(coalesce(json_extract(metadata,'$.StudyDescription'),''))<>''
        ORDER BY description LIMIT 4""", (uid,)).fetchall()
    return ' / '.join(r[0] for r in rows[:3]) + (' / …' if len(rows) > 3 else '')


def coverage(config):
    with database(config) as db:
        settings = dict(db.execute('SELECT key,value FROM settings'))
        folders = json.loads(settings.get('scan_folders', 'null'))
        sources = []
        for index, root in enumerate(config['source_roots']):
            row = db.execute("""SELECT count(*) files,
                sum(kind='dicom' AND status='ok') images,
                sum(status='error') errors, sum(status='ignored') ignored,
                count(DISTINCT CASE WHEN kind='dicom' AND status='ok'
                    THEN json_extract(metadata,'$.StudyInstanceUID') END) studies,
                count(DISTINCT CASE WHEN kind='dicom' AND status='ok'
                    AND coalesce(json_extract(metadata,'$.PatientID'),'')<>''
                    THEN json_array(json_extract(metadata,'$.IssuerOfPatientID'),json_extract(metadata,'$.PatientID')) END) patients
                FROM files WHERE active=1 AND root=?""", (root,)).fetchone()
            scope = None if folders is None else [f['relative'] or '/' for f in folders if f['root'] == index]
            sources.append(dict(row, root=root, index=index, folders=scope))
        return dict(sources=sources, last_scan=settings.get('last_scan'),
                    scan_complete=settings.get('scan_complete') == '1')
