"""One bounded, offline F5 synthesis through Atnos, followed by Whisper validation."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time

parser = argparse.ArgumentParser()
parser.add_argument('--run-name', default='', choices=['', 'verified'])
arguments = parser.parse_args()
ROOT = Path('E:/Atnos/App/reports/readiness-20260909')
if arguments.run_name:
    ROOT = ROOT / 'f5-verified'
ROOT.mkdir(parents=True, exist_ok=True)
CACHE = Path('E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data/tool-caches')
os.environ.update({
    'ATNOS_ENV_FILE': 'E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env',
    'TEMP': 'E:/Atnos/Temp', 'TMP': 'E:/Atnos/Temp',
    'HF_HOME': str(CACHE / 'huggingface'), 'TORCH_HOME': str(CACHE / 'torch'),
    'XDG_CACHE_HOME': str(CACHE / 'xdg-cache'), 'HF_HUB_OFFLINE': '1',
    'HF_HUB_DISABLE_TELEMETRY': '1', 'PYTHONIOENCODING': 'utf-8',
    'GRADIO_TEMP_DIR': 'E:/Atnos/Temp/f5-readiness',
})
from atnos.core.config import get_settings
from atnos.core.logging import redact_secrets
from atnos.production.steps.voice_synthesis import _try_f5_tts
from atnos.services.whisper_cpp_subtitle_service import WhisperCppSubtitleService

target = ROOT / 'f5-voice.json'
if target.exists():
    raise SystemExit('Probe already recorded; inspect the report before retrying.')
started = time.monotonic()
spoken = 'A small green dragon opened a red book and shared a story with his friends.'
report = {'status': 'running', 'started_at': datetime.now(timezone.utc).isoformat(),
          'expected_text': spoken, 'offline': True, 'scope': 'Existing package reference voice, CPU synthesis through Atnos.'}
target.write_text(json.dumps(report, indent=2), encoding='utf-8')
try:
    settings = get_settings().model_copy(update={'runtime_dir': ROOT / 'f5-runtime',
                                                'f5_tts_generation_timeout_sec': 240})
    audio = ROOT / 'f5-english.wav'
    report['configured_transport'] = settings.f5_tts_transport
    if settings.f5_tts_transport != 'cli':
        raise RuntimeError('This offline probe requires the configured CLI transport.')
    result = asyncio.run(_try_f5_tts(settings, text=spoken, output_path=audio))
    report['synthesis'] = result
    report['status'] = 'executed' if result.get('ok') else 'failed'
    if result.get('ok'):
        import soundfile as sf
        samples, rate = sf.read(audio)
        report.update(seconds_audio=len(samples)/rate, sample_rate=rate,
                      peak=float(abs(samples).max()), output=str(audio))
        converted = ROOT / 'f5-english-16k.wav'
        subprocess.run(['E:/Atnos/Tools/FFmpeg/bin/ffmpeg.exe', '-y', '-v', 'error',
                        '-i', str(audio), '-ar', '16000', '-ac', '1', str(converted)], check=True, timeout=30)
        whisper = WhisperCppSubtitleService(settings.model_copy(update={'whisper_language': 'en'}))
        report['whisper_runtime'] = whisper.runtime_availability().to_dict()
        if report['whisper_runtime'].get('available'):
            subtitle = ROOT / 'f5-english.srt'
            proc = subprocess.run(whisper.build_command(audio_path=converted, output_path=subtitle),
                                  capture_output=True, encoding='utf-8', errors='replace', timeout=180)
            (ROOT / 'f5-whisper.log').write_text(proc.stdout + '\n' + proc.stderr, encoding='utf-8')
            report['whisper_exit_code'] = proc.returncode
            report['transcript'] = subtitle.read_text(encoding='utf-8') if subtitle.exists() else None
except Exception as exc:
    report.update(status='error', error_type=type(exc).__name__, error=redact_secrets(str(exc))[:600])
report['seconds'] = round(time.monotonic()-started, 2)
target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False), flush=True)
raise SystemExit(0 if report['status'] == 'executed' else 1)
