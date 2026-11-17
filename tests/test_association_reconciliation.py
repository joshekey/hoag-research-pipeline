import json
import os
import unittest
from unittest.mock import patch
import pydicom
import engine
import report_browser
import txt_review
import test_workflow as workflow
from store import database


class AssociationReconciliationTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    study = workflow.WorkflowTests.study

    def select(self, legacy=False):
        with database(self.config) as db:
            rid = db.execute("SELECT id FROM files WHERE kind='report'").fetchone()[0]
        revision = workflow.revision(self.config, self.uid)
        if legacy:
            txt_review.associate(self.config, self.uid, rid, 'Verified synthetic examination', True,
                                 'synthetic', expected_revision=revision)
        else:
            preview = report_browser.preview(self.config, 'txt', str(rid))
            report_browser.associate(self.config, self.uid, dict(preview, confirm=True, review_revision=revision), 'synthetic')
        return rid

    def assert_cleared(self, rid):
        self.assertIsNone(self.study()['report_id'])
        self.assertFalse(report_browser.authorized(self.config, self.uid, rid))
        with database(self.config) as db:
            for table in ('browser_associations', 'txt_associations'):
                self.assertEqual(db.execute(f'SELECT count(*) FROM {table} WHERE uid=?', (self.uid,)).fetchone()[0], 0)

    def test_unchanged_scan_and_forced_reindex_preserve_selection_but_not_approval(self):
        rid = self.select()
        engine.prepare(self.config, self.uid, rid, nlp=workflow.NoNLP())
        engine.approve(self.config, self.uid, self.study()['sanitized'], [], 'Synthetic review',
                       expected_revision=workflow.revision(self.config, self.uid))
        for force in (False, True):
            with self.subTest(force=force):
                before = workflow.revision(self.config, self.uid)
                engine.scan(self.config, force=force)
                self.assertEqual(self.study()['report_id'], rid)
                self.assertEqual(self.study()['state'], 'needs_review')
                self.assertIsNone(self.study()['approved_hash'])
                self.assertIsNone(self.study()['sanitized'])
                self.assertTrue(report_browser.authorized(self.config, self.uid, rid))
                self.assertNotEqual(workflow.revision(self.config, self.uid), before)

    def test_legacy_selection_is_preserved_and_switching_clears_other_authorization(self):
        rid = self.select()
        self.select(legacy=True)
        self.assertFalse(report_browser.authorized(self.config, self.uid, rid))
        engine.scan(self.config)
        self.assertEqual(self.study()['report_id'], rid)
        self.select()
        with database(self.config) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM txt_associations').fetchone()[0], 0)

    def test_changed_or_removed_report_clears_both_selection_and_authorization(self):
        rid = self.select()
        self.report.write_text('Changed synthetic report', encoding='utf-8')
        engine.scan(self.config)
        self.assert_cleared(rid)
        rid = self.select()
        self.report.unlink()
        engine.scan(self.config)
        self.assert_cleared(rid)

    def test_same_size_and_time_report_change_is_not_preserved(self):
        rid = self.select()
        stat = self.report.stat()
        self.report.write_bytes(self.report.read_bytes().replace(b'Clear lungs', b'Other lungs'))
        os.utime(self.report, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        engine.scan(self.config)
        self.assert_cleared(rid)

    def test_changed_study_and_conflicting_study_clear_association(self):
        rid = self.select()
        ds = pydicom.dcmread(self.path); ds.SeriesDescription = 'Changed synthetic series'
        ds.save_as(self.path, enforce_file_format=True)
        engine.scan(self.config)
        self.assert_cleared(rid)
        rid = self.select()
        second = self.sources[0] / 'second.dcm'
        ds = workflow.image(second, self.uid, self.series); ds.StudyDate = '20210102'
        ds.save_as(second, enforce_file_format=True)
        engine.scan(self.config)
        self.assert_cleared(rid)
        self.assertEqual(self.study()['state'], 'conflict')

    def test_deleted_study_does_not_leave_an_association(self):
        self.select()
        self.path.unlink()
        engine.scan(self.config)
        with database(self.config) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM studies').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM browser_associations').fetchone()[0], 0)

    def test_sql_selection_survives_file_scan_only_while_broker_reference_is_valid(self):
        sql = dict(source='sql', ref='synthetic', text='Findings: Synthetic report', hash='synthetic-hash')
        with patch('report_browser.request_broker', return_value=sql):
            result = report_browser.associate(self.config, self.uid,
                dict(sql, confirm=True, review_revision=workflow.revision(self.config, self.uid)), 'synthetic')
            engine.scan(self.config)
            self.assertEqual(self.study()['report_id'], result['report_id'])
            self.assertTrue(report_browser.authorized(self.config, self.uid, result['report_id']))
        with patch('report_browser.request_broker', side_effect=ValueError('SQL index replaced')):
            engine.scan(self.config)
        self.assert_cleared(result['report_id'])
