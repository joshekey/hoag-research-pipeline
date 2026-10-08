"""Bounded, heuristic SQL schema discovery. Never connects to a DB or executes SQL."""
import re

LIMIT = 32 * 1024 * 1024
IDENTIFIER = r'(?:"(?:[^"]|"")*"|`(?:[^`]|``)*`|\[(?:[^\]]|\]\])*\]|[A-Za-z_][A-Za-z_0-9$]*)'
USEFUL = re.compile(r'accession|study.*uid|patient.*id|\bmrn\b|report|file.*path|filename|document', re.I)


def without_values(text):
    """Remove comments and SQL value strings before looking for DDL identifiers."""
    out, i = [], 0
    while i < len(text):
        if text.startswith('--', i):
            end = text.find('\n', i)
            i = len(text) if end < 0 else end
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            i = len(text) if end < 0 else end + 2
            out.append(' ')
        elif text[i] == "'":
            i += 1
            while i < len(text):
                if text[i] == '\\':
                    i += 2
                elif text[i] == "'":
                    if i + 1 < len(text) and text[i + 1] == "'":
                        i += 2
                    else:
                        i += 1
                        break
                else:
                    i += 1
            out.append(' NULL ')
        elif text[i] == '$' and (m := re.match(r'\$[A-Za-z_0-9]*\$', text[i:i + 130])):
            end = text.find(m[0], i + len(m[0]))
            i = len(text) if end < 0 else end + len(m[0])
            out.append(' NULL ')
        else:
            out.append(text[i])
            i += 1
    return ''.join(out)


def name(value):
    return value.strip().strip('"`[]')[:256]


def inspect(path):
    with path.open('rb') as source:
        raw = source.read(LIMIT + 1)
    truncated = len(raw) > LIMIT
    raw = raw[:LIMIT]
    encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    text = raw.decode(encoding, errors='replace')
    # PostgreSQL COPY data is not SQL and must not be interpreted as DDL.
    text = re.sub(r'(?ims)^\s*COPY\b[^\n]*\bFROM\s+stdin\s*;.*?^\\\.\s*$', '', text)
    text = without_values(text)
    pattern = re.compile(r'(?im)^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(' + IDENTIFIER + r'(?:\s*\.\s*' + IDENTIFIER + r')?)\s*\(')
    tables = []
    for match in pattern.finditer(text):
        if len(tables) >= 200:
            truncated = True
            break
        i, start, depth, parts = match.end(), match.end(), 1, []
        quote = None
        while i < len(text) and i - start < 1024 * 1024:
            char = text[i]
            if quote:
                if char == quote:
                    if i + 1 < len(text) and text[i + 1] == quote:
                        i += 1
                    else:
                        quote = None
            elif char in ('"', '`', '['):
                quote = ']' if char == '[' else char
            elif char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
                if depth == 0:
                    parts.append(text[start:i])
                    break
            elif char == ',' and depth == 1:
                parts.append(text[start:i])
                start = i + 1
            i += 1
        if depth:
            truncated = True
            continue
        columns = []
        for part in parts[:1000]:
            column = re.match(r'\s*(' + IDENTIFIER + r')\s+', part)
            if column and name(column[1]).upper() not in ('CONSTRAINT', 'PRIMARY', 'FOREIGN', 'UNIQUE', 'CHECK', 'KEY', 'INDEX'):
                columns.append(name(column[1]))
        tables.append({'table': name(match[1]), 'columns': columns,
                       'potential_link_fields': [c for c in columns if USEFUL.search(c)]})
    return {'tables': tables, 'bytes_examined': len(raw), 'limited': truncated,
            'method': 'Heuristic CREATE TABLE inspection; no rows returned and no SQL executed'}
