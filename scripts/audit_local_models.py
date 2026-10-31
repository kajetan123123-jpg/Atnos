"""Run bounded, local-only model probes; write reproducible evidence on E:."""
from pathlib import Path
import base64
import json
import time
import urllib.request

ROOT = Path('E:/Atnos/App/reports/readiness-20260909')
RESULTS = ROOT / 'local-model-probes.json'
results = []


def call(route, payload=None, timeout=300):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request('http://127.0.0.1:11434' + route, data=body,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def probe(name, fn):
    start = time.monotonic()
    try:
        details = fn()
        row = {'test': name, 'transport': 'ok', 'details': details}
    except Exception as exc:
        row = {'test': name, 'transport': 'failed', 'error': str(exc)}
    row['seconds'] = round(time.monotonic() - start, 2)
    results.append(row)
    RESULTS.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(row, ensure_ascii=False), flush=True)


def text_probe(model):
    response = call('/api/chat', {'model': model, 'think': False, 'stream': False, 'keep_alive': 0,
        'messages': [{'role': 'user', 'content': 'Answer in English in one sentence: If I save 25 dollars every week for four weeks, how much have I saved? Do not discuss investing.'}],
        'options': {'temperature': 0, 'num_ctx': 4096, 'num_predict': 100}})
    text = response['message']['content']
    return {'model': model, 'response': text, 'mentions_expected_100': '100' in text,
            'tokens': response.get('eval_count'), 'eval_seconds': response.get('eval_duration', 0) / 1e9}


def embedding_probe():
    response = call('/api/embed', {'model': 'bge-m3', 'input': ['A child finds a friendly dragon.',
        'A little girl meets a kind dragon.', 'How to calculate a savings budget.'], 'keep_alive': 0})
    vectors = response['embeddings']
    import math
    def similarity(a, b):
        return sum(x*y for x,y in zip(a,b)) / math.sqrt(sum(x*x for x in a)*sum(y*y for y in b))
    same = similarity(vectors[0], vectors[1])
    different = similarity(vectors[0], vectors[2])
    return {'dimensions': [len(v) for v in vectors], 'similar_story': same,
            'different_topic': different, 'correct_ranking': same > different}


def vision_probe():
    from PIL import Image, ImageDraw
    im = Image.new('RGB', (512, 512), 'white')
    draw = ImageDraw.Draw(im)
    draw.rectangle((45, 150, 220, 325), fill='red')
    draw.ellipse((290, 175, 440, 325), fill='blue')
    image = ROOT / 'vision-reference.png'
    im.save(image)
    response = call('/api/chat', {'model': 'qwen3-vl:8b-instruct', 'stream': False, 'keep_alive': 0,
        'messages': [{'role': 'user', 'content': 'Describe the two colored shapes and their left/right positions in one English sentence.',
                      'images': [base64.b64encode(image.read_bytes()).decode()]}],
        'options': {'temperature': 0, 'num_ctx': 4096, 'num_predict': 120}})
    text = response['message']['content']
    return {'response': text, 'mentions_colors_and_shapes': all(v in text.lower() for v in ['red', 'blue', 'square', 'circle'])}


if __name__ == '__main__':
    ROOT.mkdir(exist_ok=True, parents=True)
    probe('ollama_models', lambda: {'models': [m['name'] for m in call('/api/tags')['models']]})
    probe('qwen3_8b_text', lambda: text_probe('qwen3:8b'))
    probe('qwen3_14b_text', lambda: text_probe('qwen3:14b'))
    probe('bge_m3_embedding', embedding_probe)
    probe('qwen3_vl_vision', vision_probe)
