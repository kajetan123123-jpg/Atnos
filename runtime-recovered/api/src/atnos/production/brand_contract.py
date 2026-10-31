"""Immutable brand brief and supervised external image hand-off contracts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atnos.production.project_profile import content_hash


class BrandBeat(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^s0[1-9]$')
    start: float = Field(ge=0, le=45)
    end: float = Field(gt=0, le=45)
    direction: str = Field(min_length=5, max_length=1500)
    spoken: bool
    max_words: int = Field(ge=0, le=30)
    pose: Literal['thinking', 'explain', 'skeptical', 'pointing', 'none']
    visual: Literal['raise', 'tokens', 'costs', 'extras', 'flows', 'seed', 'branches', 'gap', 'brand']


class BrandKit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal[1]
    channel: Literal['wealth_blueprint']
    character_id: Literal['WB_ALEX_01']
    language: Literal['en']
    cta: Literal[False]
    duration: Literal[45]
    style: str = Field(min_length=100, max_length=6000)
    character: str = Field(min_length=100, max_length=3000)
    palette: dict[str, str]
    reference_file: str
    brief_file: str
    voice: Literal['am_michael']
    image_provider: Literal['codex_imagegen_supervised_relay']
    poses: dict[str, str]
    beats: list[BrandBeat] = Field(min_length=9, max_length=9)
    token_allocation: dict[str, int]

    @model_validator(mode='after')
    def timeline(self):
        if [b.id for b in self.beats] != [f's{i:02}' for i in range(1,10)]:
            raise ValueError('Nine ordered stable scene IDs are required')
        cursor = 0
        for beat in self.beats:
            if beat.start != cursor or beat.end <= beat.start or beat.spoken != bool(beat.max_words):
                raise ValueError('Timeline must be continuous and voice bounds must match spoken scenes')
            cursor = beat.end
        if cursor != self.duration or set(self.poses) != {'thinking','explain','skeptical','pointing'}:
            raise ValueError('Incomplete brand timeline or required Alex poses')
        if self.token_allocation != {'housing':2,'car':1,'phone':1,'dining':1,'extras':1,'keep':1}:
            raise ValueError('The approved illustrative seven-token budget must conserve value')
        return self


def freeze_brand(settings, key):
    from atnos.production.order_store import file_hash
    root = (settings.data_dir / 'brand-kits').resolve()
    path = (root / key / 'brand.json').resolve()
    if not path.is_relative_to(root):
        raise ValueError('Brand kit escapes Atnos data')
    kit = BrandKit.model_validate_json(path.read_text(encoding='utf-8')).model_dump(mode='json')
    files = {}
    for name in ('reference_file','brief_file'):
        target = (path.parent / kit[name]).resolve()
        if not target.is_relative_to(path.parent) or not target.is_file():
            raise ValueError('Brand source must be inside its kit directory')
        files[name] = {'path':str(target),'sha256':file_hash(target)}
    return {'key':key,'contract':kit,'sources':files,'contract_hash':content_hash(kit)}


def verified_asset(root: Path, request: dict) -> Path | None:
    from PIL import Image
    from atnos.production.order_store import file_hash
    target = root / 'brand_assets' / (request['asset_id'] + '.json')
    if not target.is_file():
        return None
    receipt = json.loads(target.read_text(encoding='utf-8'))
    if receipt.get('request_hash') != content_hash(request):
        raise ValueError('Image receipt belongs to another generation request')
    image = (root / receipt['relative_file']).resolve()
    if not image.is_relative_to((root / 'brand_assets').resolve()) or not image.is_file():
        raise ValueError('Image receipt escapes this order')
    if file_hash(image) != receipt.get('sha256') or receipt.get('provider') != 'codex_imagegen_supervised_relay':
        raise ValueError('Image changed or has no declared generation provenance')
    with Image.open(image) as im:
        if min(im.size) < 768:
            raise ValueError('Alex artwork requires a large image')
        if request.get('output_mode')=='navy_background':
            return image
        if im.mode != 'RGBA':
            raise ValueError('Alex artwork requires a large RGBA image')
        alpha = im.getchannel('A')
        if alpha.getextrema() != (0,255):
            raise ValueError('Alex artwork requires genuine transparent background')
    return image


def active_request(root: Path, pose: str, initial: dict) -> dict:
    path=root/'image_revisions'/(pose+'.json')
    if not path.is_file():return initial
    value=json.loads(path.read_text(encoding='utf-8'))
    if value.get('initial_request_hash')!=content_hash(initial):
        raise ValueError('Image revision does not match its initial request')
    return value
