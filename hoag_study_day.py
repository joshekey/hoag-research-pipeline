"""Convert explicit report service-date fields to days since 2014-01-01."""
import re
from datetime import date, datetime

BASE_DATE = date(2014, 1, 1)
POLICY = 'patient-identifiers-v6-service-day-20140101'
FIELD = re.compile(r'(?im)^(?P<label>[ \t]*(?:date[ \t]+of[ \t]+service|service[ \t]+date|study[ \t]+date|DOS)[ \t]*[:=][ \t]*)(?P<rest>[^\r\n]*)')
TOKEN = re.compile(r'(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}|\d{8}|[A-Za-z]+[ \t]+\d{1,2},?[ \t]+\d{4}|\d{1,2}[ \t]+[A-Za-z]+[ \t]+\d{4})(?![\w/.-])')


def day_number(value):
    value = value.strip()
    if re.fullmatch(r'\d{8}', value):
        parsed = datetime.strptime(value, '%Y%m%d').date()
    elif re.fullmatch(r'\d{4}[-/.]\d{1,2}[-/.]\d{1,2}', value):
        parsed = date(*map(int, re.split(r'[-/.]', value)))
    elif re.fullmatch(r'\d{1,2}[-/.]\d{1,2}[-/.]\d{4}', value):
        month, day, year = map(int, re.split(r'[-/.]', value))
        parsed = date(year, month, day)
    else:
        normalized = re.sub(r'[ \t]+', ' ', value.replace(',', '')).strip()
        for fmt in ('%B %d %Y', '%b %d %Y', '%d %B %Y', '%d %b %Y'):
            try:
                parsed = datetime.strptime(normalized, fmt).date()
                break
            except ValueError:
                pass
        else:
            raise ValueError('unrecognized_service_date')
    return (parsed - BASE_DATE).days


def shift_date_of_service(text):
    """Change only the date immediately after an explicit service/study label.

    Unknown, ambiguous or already-redacted labeled values hold the report via
    ValueError. A report with no matching label is returned unchanged. Timestamp,
    fax, comparison and dictated fields are preserved byte-for-byte as text.
    """
    def replace(match):
        rest = match.group('rest')
        if re.match(r'Day[ \t]+-?\d+(?![\w.-])', rest, re.I):
            return match.group()
        if not rest.strip():
            return match.group()
        token = TOKEN.match(rest)
        if token is None:
            raise ValueError('service_date_requires_review')
        result = day_number(token.group())
        return match.group('label') + 'Day ' + str(result) + rest[token.end():]
    return FIELD.sub(replace, text)
