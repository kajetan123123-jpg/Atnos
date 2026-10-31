"""Explicit stop via the same idle/ownership guards used by Atnos."""
import argparse
import json
from atnos.core.config import get_settings
from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8188)
    args = parser.parse_args()
    settings = get_settings()
    settings.comfyui_base_url = f'http://127.0.0.1:{args.port}'
    result = ComfyUiRuntimeService(settings).stop_server(require_idle=True)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result.get('stopped') else 1)
