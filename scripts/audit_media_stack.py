"""Exercise the same local media services used by Atnos, without publishing."""
from pathlib import Path
import json
import os
import sys
import time

ROOT = Path('E:/Atnos/App/reports/readiness-20260909')
os.environ['ATNOS_ENV_FILE'] = 'E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env'
os.environ['TEMP'] = os.environ['TMP'] = 'E:/Atnos/Temp'
os.environ['HF_HOME'] = 'E:/Atnos/ToolCaches/huggingface'
os.environ['TORCH_HOME'] = 'E:/Atnos/ToolCaches/torch'
os.environ['XDG_CACHE_HOME'] = 'E:/Atnos/ToolCaches'

from atnos.core.config import get_settings


def settings():
    return get_settings().model_copy(update={'runtime_dir': ROOT / 'media-runtime',
        'comfyui_default_checkpoint': 'sd_xl_turbo_1.0_fp16.safetensors',
        'comfyui_base_url': 'http://127.0.0.1:8188', 'comfyui_generation_timeout_s': 360})


def image_probe():
    from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationService
    result = ComfyUiImageGenerationService(settings()).generate_background(
        prompt='A friendly small green dragon sitting beside a red storybook in a cozy forest clearing, warm sunlight, children storybook illustration, one dragon, no text, no letters',
        output_path=ROOT / 'comfy-dragon.png', width=512, height=512, seed=20260909,
        steps_override=4, cfg_scale_override=1.0)
    return {'backend': result.backend, 'prompt_id': result.prompt_id,
            'output': str(result.output_path), 'evidence': str(result.evidence_path)}


def voice_probe():
    import subprocess
    import soundfile as sf
    from atnos.services.kokoro_local_runtime_service import KokoroLocalRuntimeService
    from atnos.services.whisper_cpp_subtitle_service import WhisperCppSubtitleService
    text = 'A small green dragon found a red storybook beside the old oak tree. He opened it carefully and began to read.'
    config = settings()
    raw = ROOT / 'kokoro-english.wav'
    details = KokoroLocalRuntimeService(config).synthesize_to_wav(text=text, output_path=raw, lang='en-us')
    data, rate = sf.read(raw)
    wav = ROOT / 'kokoro-english-16k.wav'
    ffmpeg = 'E:/Atnos/Tools/FFmpeg/bin/ffmpeg.exe'
    subprocess.run([ffmpeg, '-y', '-v', 'error', '-i', str(raw), '-ar', '16000', '-ac', '1', str(wav)], check=True, timeout=30)
    whisper = WhisperCppSubtitleService(config.model_copy(update={'whisper_language': 'en'}))
    availability = whisper.runtime_availability()
    subtitle = ROOT / 'kokoro-english.srt'
    out = {'synthesis': details, 'expected_text': text, 'seconds': len(data)/rate,
           'sample_rate': rate, 'peak': float(abs(data).max()), 'whisper_runtime': availability.to_dict()}
    if availability.available:
        proc = subprocess.run(whisper.build_command(audio_path=wav, output_path=subtitle),
                              capture_output=True, timeout=240, encoding='utf-8', errors='replace')
        (ROOT / 'whisper-probe.log').write_text(proc.stdout + '\n' + proc.stderr, encoding='utf-8')
        out['whisper_exit_code'] = proc.returncode
        out['transcript'] = subtitle.read_text(encoding='utf-8') if subtitle.exists() else None
    return out


def inventory():
    s = settings()
    names = ['comfyui_install_dir', 'comfyui_checkpoint_dir', 'whisper_cpp_exe', 'whisper_model_path',
             'kokoro_model_path', 'kokoro_voices_path', 'f5_tts_runtime_python', 'f5_tts_install_dir',
             'f5_tts_reference_audio', 'ollama_model', 'ollama_base_url']
    out = {}
    for name in names:
        value = getattr(s, name, None)
        if value is not None:
            out[name] = str(value)
    out['provider_keys_configured'] = {name: bool(getattr(s, name, None)) for name in
        ['openai_api_key', 'anthropic_api_key', 'elevenlabs_api_key', 'pexels_api_key', 'pixabay_api_key']}
    return out


if __name__ == '__main__':
    mode = sys.argv[1]
    start = time.monotonic()
    report = {'probe': mode, 'status': 'started'}
    dest = ROOT / f'media-{mode}.json'
    dest.write_text(json.dumps(report), encoding='utf-8')
    try:
        report.update(status='executed', result={'image': image_probe, 'voice': voice_probe, 'inventory': inventory}[mode]())
    except Exception as exc:
        from atnos.core.logging import redact_secrets
        report.update(status='failed', error=redact_secrets(str(exc)), exception=type(exc).__name__)
    report['seconds'] = round(time.monotonic()-start, 2)
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)
    sys.exit(1 if report['status'] == 'failed' else 0)
