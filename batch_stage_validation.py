"""Independent read-back checks for restricted staging, not release certification."""
import numpy as np
import pydicom
from store import research_uid


def validate(source, target, label, config):
    ds = pydicom.dcmread(target)
    if str(ds.PatientName) != label or str(ds.PatientID) != label: raise ValueError('identity')
    if str(ds.get('PatientAge', '')) != str(source.get('PatientAge', '')): raise ValueError('age')
    original_uids = {str(v) for e in source.iterall() if e.VR == 'UI'
        and 'ClassUID' not in e.keyword and e.keyword != 'TransferSyntaxUID'
        for v in (list(e.value) if e.VM > 1 else [e.value])}
    mapped = {research_uid(config, v) for v in original_uids}
    for e in ds.iterall():
        if e.tag.is_private or 0x6000 <= e.tag.group <= 0x60ff: raise ValueError('private_or_overlay')
        if e.VR in ('DA', 'DT', 'TM', 'AE', 'UR') and e.value: raise ValueError('retained_identifier')
        if e.VR == 'PN' and e.value and str(e.value) != label: raise ValueError('name')
        if e.tag.group == 0x0010 and e.tag.element not in (0x0010, 0x0020, 0x1010, 0x2212):
            if e.value: raise ValueError('demographic')
        if e.VR in ('LO','SH','LT','ST','UT','UC') and e.value and e.keyword not in (
            'PatientID', 'DeidentificationMethod'): raise ValueError('free_text')
        if e.VR == 'UI' and e.value and 'ClassUID' not in e.keyword and e.keyword != 'TransferSyntaxUID':
            if any(str(v) not in mapped for v in (list(e.value) if e.VM > 1 else [e.value])): raise ValueError('uid')
    for tag in ('StudyInstanceUID','SeriesInstanceUID','SOPInstanceUID'):
        if str(ds.get(tag)) != research_uid(config, str(source.get(tag))): raise ValueError('membership')
    if ds.file_meta.MediaStorageSOPInstanceUID != ds.SOPInstanceUID: raise ValueError('file_meta')
    if ds.file_meta.MediaStorageSOPClassUID != ds.SOPClassUID: raise ValueError('file_meta_class')
    if ds.preamble != b'\0'*128: raise ValueError('preamble')
    a, b = source.pixel_array, ds.pixel_array
    if a.shape != b.shape or not np.array_equal(a, b): raise ValueError('pixels')
    return True
