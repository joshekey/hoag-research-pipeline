"""Offline clinical DICOMweb retrieval core; NO HTTP ROUTES OR SERVER.

This module must only be invoked AFTER a separate hospital-authenticated,
independently authorized study context is established. It cannot authenticate
users, issue tokens, authorize browser sessions, or approve/export research data.

Returned DICOM bytes and metadata contain PHI. Never print, log, cache outside
restricted process memory, copy into a repository, or expose before gateway audit.
"""
import io
import sqlite3
from pathlib import Path

import pydicom

from ohif_poc.scoped_catalog import scoped_catalog, scoped_instance

MAX_INSTANCE_BYTES = 512 * 1024 * 1024


def series_counts(config, requested_study, authorized_study):
    """Return internal (SeriesInstanceUID, count) pairs, without PHI."""
    indexed = scoped_catalog(config, requested_study, authorized_study)
    counts = {}
    for series, _sop in indexed:
        counts[series] = counts.get(series, 0) + 1
    return tuple(sorted(counts.items()))


def instance_metadata(config, requested_study, authorized_study, series, sop):
    """Read only source header and return DICOM JSON metadata, never pixel bytes.

    Metadata IS PHI and must only be returned through a separately protected
    clinical gateway. BulkDataURI generation belongs in that gateway because
    it must be bound to the authenticated caller's study scope.
    """
    path = scoped_instance(config, requested_study, authorized_study, series, sop)
    header = pydicom.dcmread(path, stop_before_pixels=True)
    if any(str(header.get(k, "")) != v for k, v in (
        ("StudyInstanceUID", requested_study),
        ("SeriesInstanceUID", series),
        ("SOPInstanceUID", sop),
    )):
        raise ValueError("Source identifiers changed")
    if getattr(header, "PixelData", None) is not None:
        raise ValueError("Pixel data unexpectedly in header")
    return header.to_json_dict()


def instance_bytes(config, requested_study, authorized_study, series, sop,
                   max_bytes=MAX_INSTANCE_BYTES):
    """Return exact source DICOM bytes after catalog/path/header validation.

    Bounded read and source re-stat detect common concurrent changes. This
    primitive performs no de-identification. Never send to an untrusted client.
    """
    if type(max_bytes) is not int or not 132 <= max_bytes <= MAX_INSTANCE_BYTES:
        raise ValueError("Invalid DICOM size limit")
    mapping = scoped_catalog(config, requested_study, authorized_study)
    from ohif_poc.scoped_catalog import _uid
    key = (_uid(series), _uid(sop))
    if key not in mapping:
        raise LookupError("Instance unavailable")
    row = mapping[key]
    if type(row.get("size")) is not int or row["size"] > max_bytes:
        raise ValueError("DICOM instance exceeds the approved size limit")
    path = scoped_instance(config, requested_study, authorized_study, series, sop)
    with path.open("rb") as f:
        original = f.read(max_bytes + 1)
    if len(original) != row["size"] or len(original) > max_bytes:
        raise ValueError("DICOM source changed during retrieval")
    # Verify actual returned bytes, rather than trusting a separate earlier
    # header read. Never change the returned raw source dataset.
    header = pydicom.dcmread(io.BytesIO(original), stop_before_pixels=True)
    if (str(header.get("StudyInstanceUID", "")),
        str(header.get("SeriesInstanceUID", "")),
        str(header.get("SOPInstanceUID", ""))) != (
        requested_study, series, sop
    ):
        raise ValueError("DICOM source identity mismatch")
    # Source path containment/mount and index stat invariants are rechecked.
    import engine
    engine.source_path(config, row)
    return original
