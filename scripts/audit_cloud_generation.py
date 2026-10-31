"""Bounded live probes through Atnos adapters; no account mutation or publication."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path('E:/Atnos/App/reports/readiness-20260909')
os.environ['ATNOS_ENV_FILE'] = 'E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env'
os.environ['TEMP'] = os.environ['TMP'] = 'E:/Atnos/Temp'
from atnos.core.config import get_settings
from atnos.core.logging import redact_secrets

s = get_settings().model_copy(update={'runtime_dir': ROOT / 'media-runtime'})


def openai():
    from atnos.db.models.enums import ModelTask
    from atnos.providers.openai_compatible_cloud import OpenAICompatibleCloudProviderAdapter
    if not s.openai_api_key:
        raise ValueError('OpenAI key is not configured')
    os.environ['OPENAI_API_KEY'] = s.openai_api_key
    adapter = OpenAICompatibleCloudProviderAdapter(provider_key='openai', label='OpenAI',
        app_root=Path('E:/Atnos/App'), api_key_env_var='OPENAI_API_KEY',
        default_base_url='https://api.openai.com', model_env_prefix='OPENAI')
    model = adapter._resolve_model_name(ModelTask.GENERAL_CHAT, model_config_runtime_name=None)
    result = adapter.execute(task=ModelTask.GENERAL_CHAT,
        context=SimpleNamespace(provider_prompt='Reply with exactly ATNOS_CONNECTION_OK and nothing else.'),
        provider=SimpleNamespace(endpoint_url=None),
        model_config=SimpleNamespace(runtime_model_name=model, max_output_tokens=512))
    return {'model': model, 'response': result.content, 'correct_marker': result.content.strip() == 'ATNOS_CONNECTION_OK',
            'usage': result.usage.to_dict(), 'finish_reason': result.finish_reason,
            'scope': 'One bounded text response through the real Atnos cloud adapter.'}


def voice():
    from atnos.production.project_profile import load_project_profile
    from atnos.production.steps import order_voice
    profile = load_project_profile(s, 'wealth_blueprint')
    config = profile['production']['voice']
    settings = order_voice.voice_settings(s, config)
    context = SimpleNamespace(artifact_dir=ROOT / 'cloud-voice', artifacts={'project_profile': profile})
    text = 'A small green dragon found a red storybook beside the old oak tree. He opened it carefully and began to read.'
    segment = {'id': 's01', 'text': text}
    result = asyncio.run(order_voice.synthesize(settings, context, segment, '', ''))
    narration = result.payload
    recognized = order_voice.transcribe(settings, Path(narration['output_file']), context.artifact_dir / 'recognized.srt')
    return {'provider': 'elevenlabs', 'profile': 'wealth_blueprint', 'model': config['model'],
            'output': narration['output_file'], 'duration_seconds': narration['duration_seconds'],
            'aligned_words': len(narration['word_timestamps']), 'qa': order_voice.speech_match(text, recognized, []),
            'scope': 'One short English sample using the saved channel voice, alignment, and independent Whisper verification.'}


def gemini():
    from atnos.db.models.enums import ModelTask
    from atnos.providers.google_gemini import GoogleGeminiProviderAdapter
    adapter = GoogleGeminiProviderAdapter(app_root=Path('E:/Atnos/App'), settings=s)
    model = adapter._resolve_model_name(ModelTask.GENERAL_CHAT, model_config_runtime_name=None)
    result = adapter.execute(task=ModelTask.GENERAL_CHAT,
        context=SimpleNamespace(provider_prompt='Reply with exactly ATNOS_CONNECTION_OK and nothing else.'),
        provider=SimpleNamespace(endpoint_url=None),
        model_config=SimpleNamespace(runtime_model_name=model, max_output_tokens=512))
    return {'model': model, 'response': result.content, 'correct_marker': result.content.strip() == 'ATNOS_CONNECTION_OK',
            'usage': result.usage.to_dict(), 'scope': 'One bounded text response through the real Atnos Gemini adapter.'}


if __name__ == '__main__':
    mode = sys.argv[1]
    target = ROOT / f'cloud-{mode}.json'
    if target.exists() and '--retry' not in sys.argv:
        print(target.read_text(encoding='utf-8'))
        raise SystemExit(0)
    started = time.monotonic()
    report = {'probe': mode, 'status': 'started'}
    target.write_text(json.dumps(report), encoding='utf-8')
    try:
        report.update(status='executed', result={'openai': openai, 'voice': voice, 'gemini': gemini}[mode]())
    except Exception as exc:
        report.update(status='failed', error_type=type(exc).__name__, error=redact_secrets(str(exc))[:600])
    report['seconds'] = round(time.monotonic()-started, 2)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(1 if report['status'] == 'failed' else 0)
