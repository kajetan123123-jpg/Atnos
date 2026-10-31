"""One durable ComfyUI request per story illustration, without silent resubmission."""
from io import BytesIO
import json
from pathlib import Path
import re
import time
from uuid import uuid4

import httpx
from PIL import Image

from atnos.production.fiction_script import StoryScript
from atnos.production.order_store import file_hash
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json


def illustration_prompt(script: StoryScript, segment_id: str, config: dict) -> str:
    segment = next(s for s in script.segments if s.id == segment_id)
    location = next(loc for loc in script.locations if loc.id == segment.location_id)
    characters = [c for c in script.characters if c.id in segment.character_ids]
    scene = segment.visual_intent
    # A compact visual prompt puts the actual action before style instructions.
    # Expand IDs/names in one pass, so inserted descriptions are not expanded again.
    names = {c.id: c.appearance for c in characters} | {c.name: c.appearance for c in characters}
    names.update({loc.id: loc.description for loc in script.locations})
    if names:
        pattern = r'\b(?:' + '|'.join(re.escape(key) for key in sorted(names, key=len, reverse=True)) + r')\b'
        scene = re.sub(pattern, lambda match: names[match.group()], scene)
    contract = config.get('channel_contract') if isinstance(config.get('channel_contract'), dict) else {}
    visual = contract.get('visual') if isinstance(contract.get('visual'), dict) else {}
    style = str(visual.get('style') or '').strip()
    if not style:
        style = ('Gentle watercolor storybook illustration, soft warm light, clear shapes, calm atmosphere.'
                 if config['content_kind'] == 'children_story' else
                 'Cinematic suspense illustration, readable dark lighting, grounded proportions, restrained cool colors.')
    world = visual.get('world_bible') if isinstance(visual.get('world_bible'), dict) else {}
    world_hint = ''
    if world:
        world_hint = ' World bible: ' + str(world.get('world') or '')
    references = contract.get('reference_files') or []
    reference_hint = (' Use the approved character and world reference sheets from Atnos: ' + ', '.join(map(str, references)) + '.'
                      if references else '')
    return scene + ' Setting: ' + location.description + '. ' + style + '.' + world_hint + reference_hint + ' No text.'


def checkpoint_workflow(prompt: str, *, checkpoint: str, width=640, height=896, seed=1, prefix: str):
    if not re.fullmatch(r'atnos_story_[a-f0-9]{32}', prefix):
        raise ValueError('Invalid story image output prefix')
    turbo = 'turbo' in checkpoint.casefold()
    return {
        '1': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': checkpoint}},
        '2': {'class_type': 'CLIPTextEncode', 'inputs': {'text': prompt, 'clip': ['1', 1]}},
        '3': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'text, letters, watermark, logo, deformed hands, duplicate characters, extra limbs, blurry, gore, collage', 'clip': ['1', 1]}},
        '4': {'class_type': 'EmptyLatentImage', 'inputs': {'width': width, 'height': height, 'batch_size': 1}},
        '5': {'class_type': 'KSampler', 'inputs': {'seed': seed, 'steps': 4 if turbo else 24, 'cfg': 1.5 if turbo else 6.5,
            'sampler_name': 'euler', 'scheduler': 'normal', 'denoise': 1, 'model': ['1', 0],
            'positive': ['2', 0], 'negative': ['3', 0], 'latent_image': ['4', 0]}},
        '6': {'class_type': 'VAEDecode', 'inputs': {'samples': ['5', 0], 'vae': ['1', 2]}},
        '7': {'class_type': 'SaveImage', 'inputs': {'images': ['6', 0], 'filename_prefix': prefix}},
    }


def generate_illustration(settings, directory: Path, *, prompt: str, checkpoint: str, seed: int, timeout_s=600) -> dict:
    """Resume a known prompt after a restart; an uncertain POST never creates another image."""
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    record = directory / 'request.json'
    image_path = directory / 'illustration.png'
    inputs = {'prompt': prompt, 'checkpoint': checkpoint, 'seed': seed, 'width': 640, 'height': 896, 'contract': 1}
    identity = content_hash(inputs)
    if record.exists():
        state = json.loads(record.read_text(encoding='utf-8'))
        if state['input_hash'] != identity:
            raise ValueError('An illustration request already exists with different inputs')
        if state['status'] == 'complete':
            if not image_path.is_file() or file_hash(image_path) != state['sha256']:
                raise ValueError('The completed illustration changed after verification')
            return state
        if not state.get('prompt_id'):
            raise RuntimeError('An earlier image submission has an uncertain outcome; inspect ComfyUI history before any new generation')
    else:
        state = {'status': 'prepared', 'input_hash': identity, 'inputs': inputs, 'prefix': 'atnos_story_' + uuid4().hex,
                 'model_output': True, 'quality_review': 'not_performed'}
    base = settings.comfyui_base_url.rstrip('/')
    with httpx.Client(timeout=30) as client:
        if not state.get('prompt_id'):
            queue = client.get(base + '/queue')
            queue.raise_for_status()
            if queue.json().get('queue_running') or queue.json().get('queue_pending'):
                raise RuntimeError('ComfyUI is busy; no illustration was submitted')
            info = client.get(base + '/object_info/CheckpointLoaderSimple')
            info.raise_for_status()
            names = info.json()['CheckpointLoaderSimple']['input']['required']['ckpt_name'][0]
            if checkpoint not in names:
                raise ValueError('Requested illustration checkpoint is unavailable in this ComfyUI runtime')
            workflow = checkpoint_workflow(prompt, checkpoint=checkpoint, seed=seed, prefix=state['prefix'])
            write_json(directory / 'workflow.json', workflow)
            state['status'] = 'submitting'
            write_json(record, state)
            response = client.post(base + '/prompt', json={'prompt': workflow})
            response.raise_for_status()
            state.update(status='submitted', prompt_id=response.json()['prompt_id'])
            write_json(record, state)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            response = client.get(base + '/history/' + state['prompt_id'])
            response.raise_for_status()
            history = response.json().get(state['prompt_id'])
            if not history:
                time.sleep(2)
                continue
            write_json(directory / 'history.json', history)
            if history.get('status', {}).get('status_str') == 'error':
                state['status'] = 'failed'
                write_json(record, state)
                raise RuntimeError('ComfyUI failed this illustration; see retained history, no automatic resubmission')
            entries = history.get('outputs', {}).get('7', {}).get('images', [])
            if not entries:
                time.sleep(2)
                continue
            if len(entries) != 1 or not entries[0]['filename'].startswith(state['prefix']):
                raise ValueError('ComfyUI returned an unexpected output image')
            source = entries[0]
            response = client.get(base + '/view', params={key: source[key] for key in ('filename', 'subfolder', 'type')})
            response.raise_for_status()
            with Image.open(BytesIO(response.content)) as image:
                image.load()
                if image.size != (inputs['width'], inputs['height']):
                    raise ValueError('Unexpected illustration dimensions')
                image.convert('RGB').save(image_path)
            state.update(status='complete', output=str(image_path), sha256=file_hash(image_path), source=source)
            write_json(record, state)
            return state
    raise TimeoutError('Illustration observation deadline reached; resume this same saved prompt ID')
