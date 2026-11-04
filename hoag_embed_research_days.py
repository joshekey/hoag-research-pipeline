#!/usr/bin/env python3
"""Embed Hoag research day counts in a new Part 10 DICOM file."""
import argparse
from datetime import date, datetime
import os
from pathlib import Path
import re
import tempfile

import pydicom
from pydicom.datadict import add_private_dict_entries

CREATOR = "HOAG_RESEARCH_DAYS"
GROUP = 0x0011
BASELINE = date(2014, 1, 1)
FIELDS = ("StudyDate", "SeriesDate", "AcquisitionDate", "ContentDate", "AcquisitionDateTime")
add_private_dict_entries(CREATOR, {
    (GROUP << 16) | (0x1000 + i): ("SL", "1", name + " Research Day", "")
    for i, name in enumerate(FIELDS, 1)
})
add_private_dict_entries(CREATOR, {
    0x00111006: ("LO", "1", "Acquisition DateTime Suffix", ""),
})


def embed(source, output):
    ds = pydicom.dcmread(source)
    if not ds.get("StudyDate"):
        raise ValueError("StudyDate missing. Use the anonymized copy from before date conversion.")
    if any(
        elem.tag.is_private_creator and elem.tag.group == GROUP and elem.value == CREATOR
        for elem in ds
    ):
        raise ValueError("Research-day fields already present.")
    counts = {}
    suffix = ""
    for offset, name in enumerate(FIELDS, 1):
        raw = str(ds.get(name, "")).strip()
        if not raw:
            continue
        token = raw[:8] if name == "AcquisitionDateTime" else raw
        if not re.fullmatch(r"[0-9]{8}", token):
            raise ValueError(f"{name} requires a complete source date.")
        try:
            counts[offset] = (datetime.strptime(token, "%Y%m%d").date() - BASELINE).days
        except ValueError as exc:
            raise ValueError(f"Invalid calendar date in {name}.") from exc
        if name == "AcquisitionDateTime":
            suffix = raw[8:]

    block = ds.private_block(GROUP, CREATOR, create=True)
    for offset, days in counts.items():
        block.add_new(offset, "SL", days)
        setattr(ds, FIELDS[offset - 1], "")
    if suffix:
        block.add_new(6, "LO", suffix)
    if "InstitutionName" in ds:
        del ds.InstitutionName

    fd, name = tempfile.mkstemp(prefix=".hoag-dicom-", dir=output.parent)
    os.close(fd)
    try:
        ds.save_as(name)
        written = pydicom.dcmread(name)
        stored = written.private_block(GROUP, CREATOR)
        if any(stored[i].value != days for i, days in counts.items()):
            raise ValueError("Private day count did not survive serialization.")
        if suffix and stored[6].value != suffix:
            raise ValueError("Acquisition time suffix did not survive serialization.")
        if any(written.get(field) for field in FIELDS) or "InstitutionName" in written:
            raise ValueError("Date or institution cleanup did not survive serialization.")
        for field in ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID",
                      "PixelData", "FloatPixelData", "DoubleFloatPixelData"):
            if written.get(field) != ds.get(field):
                raise ValueError(f"{field} changed during serialization.")
        os.link(name, output)
    finally:
        os.unlink(name)
    return block.get_tag(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    tag = embed(args.source, args.output)
    print(f"Verified research DICOM written. Study-day tag {tag}.")
