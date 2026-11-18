"""Versioned, source-bound pixel masks. Legacy rectangle lists retain exam scope."""
import re


def entries(document):
    if isinstance(document, list):
        masks = [{'scope': 'exam', 'rect': rect} for rect in document]
    elif isinstance(document, dict) and set(document) == {'version', 'masks'} and type(document['version']) is int and document['version'] == 2:
        masks = document['masks']
    else:
        raise ValueError('Unsupported pixel mask format; expected legacy rectangles or version 2 masks')
    if not isinstance(masks, list) or len(masks) > 100:
        raise ValueError('Pixel masks must be a list of up to 100 rectangles')
    for mask in masks:
        if not isinstance(mask, dict):
            raise ValueError('Invalid pixel mask entry')
        scope = mask.get('scope')
        keys = {'scope', 'rect'}
        if scope in ('series', 'image'):
            keys.add('series_uid')
        if scope == 'image':
            keys.update(('sop_uid', 'frame'))
        if scope not in ('exam', 'series', 'image') or set(mask) != keys:
            raise ValueError('Invalid mask scope or target fields')
        for key in ('series_uid', 'sop_uid'):
            if key in mask and (not isinstance(mask[key], str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)*', mask[key]) or len(mask[key]) > 64):
                raise ValueError('Mask target must be a DICOM UID')
        if scope == 'image' and (type(mask['frame']) is not int or mask['frame'] < 0):
            raise ValueError('Mask frame must be a nonnegative integer')
        rect = mask.get('rect')
        if not isinstance(rect, list) or len(rect) != 4 or any(type(v) is not int or v < 0 for v in rect) or rect[2] <= 0 or rect[3] <= 0:
            raise ValueError('Each mask is [x, y, width, height] with nonnegative origin and positive size')
    return masks


def applies(mask, ds):
    return (mask['scope'] == 'exam' or
            (mask['series_uid'] == str(ds.get('SeriesInstanceUID', '')) and
             (mask['scope'] == 'series' or mask['sop_uid'] == str(ds.get('SOPInstanceUID', '')))))


def for_instance(document, ds):
    return [mask for mask in entries(document) if applies(mask, ds)]


def validate_bounds(ds, masks):
    if not masks:
        return
    if int(ds.get('SamplesPerPixel', 1)) != 1:
        raise ValueError('Color pixel masking is unsupported; quarantine this study')
    h, w, frames = int(ds.get('Rows', 0)), int(ds.get('Columns', 0)), int(ds.get('NumberOfFrames', 1))
    for mask in masks:
        x, y, width, height = mask['rect']
        if x + width > w or y + height > h:
            raise ValueError('Pixel mask is outside an image in its scope; adjust the mask or choose a narrower scope')
        if mask['scope'] == 'image' and mask['frame'] >= frames:
            raise ValueError('Pixel mask targets a frame that does not exist')


def validate_targets(document, headers):
    masks = entries(document)
    for mask in masks:
        targets = [ds for ds in headers if applies(mask, ds)]
        if not targets:
            raise ValueError('Pixel mask target is not in the selected exam; reload and review its current images')
        for ds in targets:
            validate_bounds(ds, [mask])


def covers_frames(masks, frames):
    specific = {m['frame'] for m in masks if m['scope'] == 'image'}
    return any(m['scope'] in ('exam', 'series') for m in masks) or (len(specific) == frames and all(0 <= frame < frames for frame in specific))
