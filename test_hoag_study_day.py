import unittest
from hoag_study_day import day_number, shift_date_of_service


class StudyDayTests(unittest.TestCase):
    def test_baseline_and_negative(self):
        self.assertEqual(day_number('01/01/2014'), 0)
        self.assertEqual(day_number('12/31/2013'), -1)
        self.assertEqual(day_number('01/02/2014'), 1)

    def test_all_supported_formats(self):
        for value in ('08/09/2018', '8/9/2018', '2018-08-09', '20180809',
                      'August 9, 2018', 'Aug 9 2018', '9 August 2018'):
            self.assertEqual(day_number(value), 1681, value)

    def test_leap_dates(self):
        self.assertEqual(day_number('03/01/2016') - day_number('02/28/2016'), 2)
        with self.assertRaises(ValueError):
            day_number('02/29/2018')

    def test_only_labeled_service_date_changes(self):
        source = ('8/10/2018 11:31 AM FROM: Fax\n'
                  'Date of Service: 08/09/2018 8:26:30 AM FAX: [REDACTED]\n'
                  'COMPARISON: 01/02/2014\nDictated: 08/10/2018\n'
                  'DOB: [REDACTED]\nFINDINGS: T12-L1, 3 mm.\n')
        expected = source.replace('Date of Service: 08/09/2018', 'Date of Service: Day 1681')
        self.assertEqual(shift_date_of_service(source), expected)

    def test_multiple_pages_and_idempotency(self):
        text = 'Date of Service: 20140101\r\nDOS: 12/31/2013\r\n'
        out = 'Date of Service: Day 0\r\nDOS: Day -1\r\n'
        self.assertEqual(shift_date_of_service(text), out)
        self.assertEqual(shift_date_of_service(out), out)

    def test_unresolved_or_ambiguous_values_hold(self):
        for value in ('[REDACTED]', '02/30/2018', '8/9/18', 'unknown'):
            with self.assertRaises(ValueError):
                shift_date_of_service('Date of Service: ' + value)


if __name__ == '__main__':
    unittest.main()
