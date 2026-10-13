"""Private diagnostic evidence, never authorization to accept or release."""
from collections import defaultdict
import hashlib
from pathlib import Path
import warnings

import pydicom
import batch_reconcile as base

IDENTITY = ('PatientName', 'PatientID', 'IssuerOfPatientID', 'PatientBirthDate')
PIXEL_TAGS = (0x7FE00010, 0x7FE00008, 0x7FE00009)
GEOMETRY = ('Rows', 'Columns', 'SamplesPerPixel', 'PhotometricInterpretation',
            'PlanarConfiguration', 'NumberOfFrames', 'BitsAllocated', 'BitsStored',
            'HighBit', 'PixelRepresentation')
INTERPRETATION = GEOMETRY + ('RescaleIntercept', 'RescaleSlope', 'RescaleType',
    'PixelSpacing', 'ImageOrientationPatient', 'ImagePositionPatient',
    'SharedFunctionalGroupsSequence', 'PerFrameFunctionalGroupsSequence',
    'ModalityLUTSequence', 'RealWorldValueMappingSequence')


def field_value(tag, value):
    if tag == 'PatientName': return base.normalize_name(value)
    if tag == 'PatientBirthDate': return base.normalize_date(value)
    return value.strip()


def identity_breakdown(objects):
    """Count affected studies directly, not all studies of an affected patient."""
    rows = []
    normalization_audit = []
    by_label = defaultdict(list)
    for obj in objects.values():
        if 'metadata' in obj:
            for label in obj['owners']: by_label[label].append(obj)
    for label, records in by_label.items():
        name_values = {r['metadata']['PatientName'] for r in records}
        pn_roles = {tuple(part.strip().upper() for part in name.split('^')) for name in name_values if '^' in name}
        if (any(any(ord(c) > 127 or c.isdigit() for c in name) for name in name_values)
            or (len(pn_roles) > 1 and len({base.normalize_name(n) for n in name_values}) == 1)):
            normalization_audit.append({'label': label, 'classification': 'lossy_name_normalization_requires_review',
                'studies': sorted({r['study'] for r in records}), 'paths': sorted({r['path'] for r in records})})
        studies = defaultdict(list)
        for record in records: studies[record['study']].append(record)
        for tag in IDENTITY:
            for scope, group_id, group in [('across_studies', label, records)] + [
                ('within_study', uid, values) for uid, values in studies.items()]:
                raw = {r['metadata'][tag] for r in group}
                populated = {field_value(tag, v) for v in raw if v.strip()}
                if len(populated) > 1: category = 'populated_variation_unresolved'
                elif '' in populated: category = 'invalid_value'
                elif any(not v.strip() for v in raw): category = 'missing_values'
                elif len(raw) > 1: category = 'normalization_only'
                else: continue
                if scope == 'across_studies' and len(studies) < 2: continue
                hints = []
                if category == 'populated_variation_unresolved':
                    issuers = {r['metadata']['IssuerOfPatientID'].strip() for r in group}
                    if tag in ('PatientID', 'IssuerOfPatientID') and len(issuers - {''}) > 1:
                        hints.append('namespace_difference_candidate_requires_authoritative_mapping')
                    if tag in ('PatientName', 'PatientBirthDate') and scope == 'across_studies':
                        stable = all(len({field_value(tag, r['metadata'][tag]) for r in rs}) == 1 for rs in studies.values())
                        if stable: hints.append('historical_change_candidate_requires_demographic_history')
                rows.append({'label': label, 'attribute': tag, 'scope': scope,
                    'group': group_id, 'classification': category, 'hints': hints,
                    'studies': sorted({r['study'] for r in group}),
                    'paths': sorted({r['path'] for r in group}),
                    'evidence': [{'path': r['path'], 'study': r['study'],
                                 'value': r['metadata'][tag],
                                 'issuer': r['metadata']['IssuerOfPatientID'],
                                 'study_date': r['metadata']['StudyDate']} for r in group]})
    aggregate = []
    keys = sorted({(r['attribute'], r['scope'], r['classification']) for r in rows})
    for tag, scope, category in keys:
        subset = [r for r in rows if (r['attribute'], r['scope'], r['classification']) == (tag, scope, category)]
        aggregate.append({'attribute': tag, 'scope': scope, 'classification': category,
            'unique_patients': len({r['label'] for r in subset}),
            'unique_studies': len({v for r in subset for v in r['studies']}),
            'unique_instance_paths': len({v for r in subset for v in r['paths']})})
    # Sharing an identifier alone is a collision candidate, not proof of identity.
    identifiers = defaultdict(list)
    for obj in objects.values():
        if 'metadata' not in obj: continue
        m = obj['metadata']
        if m['PatientID']:
            identifiers[(m['IssuerOfPatientID'], m['PatientID'])].append(obj)
    collisions = []
    for records in identifiers.values():
        labels = set().union(*(r['owners'] for r in records))
        if len(labels) > 1:
            collisions.append({'labels': sorted(labels), 'studies': sorted({r['study'] for r in records}),
                               'paths': sorted({r['path'] for r in records}),
                               'classification': 'cross_label_identifier_collision_requires_review'})
    hints = {}
    for hint in sorted({hint for r in rows for hint in r['hints']}):
        subset = [r for r in rows if hint in r['hints']]
        hints[hint] = {'unique_patients': len({r['label'] for r in subset}),
                      'unique_studies': len({v for r in subset for v in r['studies']})}
    return {'private_fields': rows, 'aggregate_fields': aggregate, 'cross_label_collisions': collisions,
            'unconfirmed_explanations': hints, 'normalization_audit': normalization_audit,
            'confirmed_cross_patient_conflicts': None,
            'confirmation_status': 'requires_authoritative_patient_evidence'}


def signature(ds, fields):
    return tuple(str(ds.get(field, '')) for field in fields)


def pixel_evidence(ds):
    present = [tag for tag in PIXEL_TAGS if tag in ds]
    if len(present) != 1:
        raise ValueError('missing_or_multiple_pixel_elements')
    tag = present[0]
    return (tag, hashlib.sha256(ds[tag].value).hexdigest(),
            str(ds.file_meta.get('TransferSyntaxUID', '')), signature(ds, GEOMETRY))


def compare_pixel_objects(datasets):
    """Distinguish stored pixels, decoded values and interpretation; no clearance."""
    evidence = [pixel_evidence(ds) for ds in datasets]
    interpretation_equal = len({signature(ds, INTERPRETATION) for ds in datasets}) == 1
    if len(set(evidence)) == 1:
        return 'same_stored_pixels_nonpixel_bytes_differ', interpretation_equal
    if len({signature(ds, GEOMETRY) for ds in datasets}) != 1:
        return 'pixel_structure_differs', interpretation_equal
    # pydicom returns numerical arrays; compare values and shape, not file encoding.
    import numpy as np
    first = datasets[0].pixel_array
    for ds in datasets[1:]:
        other = ds.pixel_array
        if first.shape != other.shape or first.dtype.kind != other.dtype.kind or not np.array_equal(first, other):
            return 'decoded_pixels_differ', interpretation_equal
    return 'same_decoded_pixels_encoding_differs', interpretation_equal


def duplicate_breakdown(duplicates, objects, config, mounted):
    results = []
    for group in duplicates:
        if group['classification'] != 'different_bytes_unresolved': continue
        row = {'sop_uid': group['sop_uid'], 'labels': group['labels'], 'studies': group['studies'],
               'paths': group['paths'], 'release_allowed': False}
        try:
            datasets = []
            before_stats = []
            for name in group['paths']:
                path = base.source_checked(name, config['source_roots'], Path(config['output_root']).resolve(), mounted)
                before = path.stat()
                obj = objects[name]
                if (before.st_size, before.st_mtime_ns) != (obj['size'], obj['mtime_ns']):
                    raise ValueError('changed_object')
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore')
                    datasets.append(pydicom.dcmread(path))
                before_stats.append((path, before.st_size, before.st_mtime_ns))
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                row['classification'], row['image_interpretation_equal'] = compare_pixel_objects(datasets)
            # Tag numbers only in summaries; values never leave private evidence.
            all_tags = set().union(*(set(ds.keys()) for ds in datasets)) - set(PIXEL_TAGS)
            row['changed_top_level_tags'] = [str(tag) for tag in sorted(all_tags)
                if len({str(ds.get(tag, '')) for ds in datasets}) > 1]
            for path, size, mtime in before_stats:
                now = path.stat()
                if (now.st_size, now.st_mtime_ns) != (size, mtime): raise ValueError('changed_object')
        except Exception as error:
            row['classification'] = 'pixel_comparison_unresolved'
            row['error_type'] = type(error).__name__
        results.append(row)
    counts = {}
    for category in sorted({r['classification'] for r in results}):
        subset = [r for r in results if r['classification'] == category]
        counts[category] = {'groups': len(subset), 'unique_patients': len({v for r in subset for v in r['labels']}),
                            'unique_studies': len({v for r in subset for v in r['studies']}),
                            'unique_instance_paths': len({v for r in subset for v in r['paths']})}
    return {'private_groups': results, 'aggregate': counts,
            'interpretation_difference_groups': sum(r.get('image_interpretation_equal') is False for r in results)}
