"""Select the authorized existing-vault image API for stopped WB trials."""
import argparse
import json
from pathlib import Path
from atnos.core.config import get_settings
from atnos.production.wb_image_api import register_image_lane, register_local_image_lane

parser = argparse.ArgumentParser()
parser.add_argument('job_ids', nargs='+')
parser.add_argument('--reference', type=Path)
parser.add_argument('--local', action='store_true')
parser.add_argument('--authorized', action='store_true')
args = parser.parse_args()
settings = get_settings()
for job_id in args.job_ids:
    if args.local:
        result = register_local_image_lane(settings, job_id, authorized=args.authorized)
    else:
        if args.reference is None:
            parser.error('--reference is required when selecting the OpenAI lane')
        result = register_image_lane(settings, job_id, reference_path=args.reference, authorized=args.authorized)
    print(json.dumps({'job_id': job_id, 'model': result['model'], 'digest': result['digest'],
                      'reference_sha256': result['reference_sha256']}, ensure_ascii=False))
