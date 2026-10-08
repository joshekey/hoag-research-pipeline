"""SQL report probe scaffold. Read-only; aggregate counts only; no SQL execution."""
import re
from collections import Counter
from pathlib import Path
HEADER = re.compile(rb'^\s*INSERT\s+(?:IGNORE\s+)?INTO\s+(?:(?:\x60[^\x60]+\x60|[A-Za-z_]\w*)\s*\.\s*)?(?:\x60([^\x60]+)\x60|([A-Za-z_]\w*))\s+VALUES\s*', re.I)
TARGETS = {
    'labdataex': (28, (('processedresult', 4),)),
    'hl7labnotes': (8, (('notes', 1),)),
    'electronichl7content': (13, (('hl7message', 1), ('newHL7Message', 9))),
    'recelectroniclabresults': (7, (('processedResult', 1),)),
    'labdata': (None, (('result', 5), ('Notes', 7), ('Addendum', 9))),
}
TERMS = (b'IMPRESSION:', b'FINDINGS:', b'MRI LUMBAR SPINE')
MAX_LINE = 32 * 1024 * 1024

class UnsupportedStatement(ValueError):
    pass

def parse_rows(data):
    i, n = 0, len(data)
    while i < n:
        while i < n and data[i] in b' \r\n\t,':
            i += 1
        if i == n or data[i] == 59:
            return
        if data[i] != 40:
            raise UnsupportedStatement('row open')
        i += 1
        row = []
        while True:
            while i < n and data[i] in b' \r\n\t':
                i += 1
            if i >= n:
                raise UnsupportedStatement('truncated field')
            if data[i] in (39, 34):
                quote = data[i]
                i += 1
                value = bytearray()
                while True:
                    if i >= n:
                        raise UnsupportedStatement('unterminated string')
                    ch = data[i]
                    i += 1
                    if ch == 92:
                        if i >= n:
                            raise UnsupportedStatement('truncated escape')
                        esc = data[i]
                        i += 1
                        value.extend({48:b'\0', 98:b'\b', 110:b'\n', 114:b'\r', 116:b'\t', 90:b'\x1a'}.get(esc, bytes([esc])))
                    elif ch == quote:
                        if i < n and data[i] == quote:
                            value.append(ch)
                            i += 1
                        else:
                            break
                    else:
                        value.append(ch)
                row.append(bytes(value))
            else:
                begin = i
                while i < n and data[i] not in (44, 41):
                    i += 1
                raw = data[begin:i].strip()
                if raw.upper() == b'NULL':
                    row.append(None)
                elif re.fullmatch(rb'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?', raw):
                    row.append(raw)
                else:
                    raise UnsupportedStatement('expression')
            while i < n and data[i] in b' \r\n\t':
                i += 1
            if i >= n:
                raise UnsupportedStatement('delimiter')
            ch = data[i]
            i += 1
            if ch == 41:
                yield tuple(row)
                break
            if ch != 44:
                raise UnsupportedStatement('field separator')

def probe(path, limit=None):
    counts = Counter()
    seen = 0
    with Path(path).open('rb') as handle:
        for line in handle:
            seen += len(line)
            if limit and seen > limit:
                counts['scan_limit'] += 1
                break
            m = HEADER.match(line[:2048])
            if not m:
                continue
            table = (m.group(1) or m.group(2)).decode('ascii').lower()
            if table not in TARGETS:
                continue
            counts[table+'.statements'] += 1
            if len(line) > MAX_LINE or not line.rstrip().endswith(b';'):
                counts[table+'.unsupported'] += 1
                continue
            width, fields = TARGETS[table]
            try:
                for row in parse_rows(line[m.end():]):
                    if (width is not None and len(row) != width) or len(row) <= max(idx for _, idx in fields):
                        counts[table+'.width_mismatch'] += 1
                        continue
                    counts[table+'.rows'] += 1
                    for name, idx in fields:
                        value = row[idx]
                        if value:
                            counts[table+'.'+name+'.populated'] += 1
                            for term in TERMS:
                                if term in value.upper():
                                    counts[table+'.'+name+'.'+term.decode('ascii')] += 1
            except UnsupportedStatement:
                counts[table+'.parse_error'] += 1
    return counts, seen

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('sql_path')
    p.add_argument('--max-mib', type=int)
    args = p.parse_args()
    counts, seen = probe(args.sql_path, args.max_mib*1048576 if args.max_mib else None)
    print('Aggregate-only SQL report probe; bytes scanned:', seen)
    for name in sorted(counts):
        print(name, counts[name])
    print('No patient values printed.')
