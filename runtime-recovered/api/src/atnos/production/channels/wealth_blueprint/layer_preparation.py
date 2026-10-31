"""Prepare reviewable display layers without changing the generated source."""
from __future__ import annotations
import json
from pathlib import Path
from PIL import Image
from atnos.production.project_manifest import file_sha256


def trim_transparent_margin(source: Path) -> dict:
    source = source.resolve()
    if source.drive.upper() != 'E:':
        raise ValueError('WB layer preparation must stay on E:')
    with Image.open(source) as original:
        if original.mode != 'RGBA':
            raise ValueError('Transparent layer required; no background removal is inferred')
        bounds = original.getchannel('A').getbbox()
        if not bounds:
            raise ValueError('Layer is fully transparent')
        left, top, right, bottom = bounds
        padding = 12
        box = (max(0, left-padding), max(0, top-padding),
               min(original.width, right+padding), min(original.height, bottom+padding))
        result = original.crop(box)
        target = source.with_name(source.stem + '_display_v1.png')
        result.save(target)
    evidence = {'transform':'transparent_margin_crop_v1', 'source':str(source),
        'source_sha256':file_sha256(source), 'path':str(target), 'sha256':file_sha256(target),
        'crop_box':list(box), 'width':result.width, 'height':result.height,
        'user_approved':False, 'appearance_review':'required'}
    target.with_suffix('.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    return evidence
