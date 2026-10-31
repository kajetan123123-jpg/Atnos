"""Prepare or execute a bounded WB voice audition; never render or publish a film."""
import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from atnos.core.config import get_settings
from atnos.production.channels.wealth_blueprint.voice_audition import (
    create_voice_audition, prepare_local_voice_audition,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--comparison', choices=[
        'voices', 'eric-intonation', 'eric-model', 'wb-alternatives', 'local-interpretation'], default='voices')
    parser.add_argument('--prepare-local', action='store_true', help='Write a draft only, without inference')
    parser.add_argument('--segment', action='append', default=[])
    parser.add_argument('--feedback', action='append', default=[])
    parser.add_argument('--request-sha256', help='Exact authorized local request hash; never implies voice approval')
    args = parser.parse_args(argv)
    if args.request_sha256 and args.comparison != 'local-interpretation':
        parser.error('--request-sha256 cannot select a paid comparison')
    if args.prepare_local:
        if args.comparison != 'local-interpretation' or args.request_sha256:
            parser.error('--prepare-local requires local-interpretation and cannot execute a request')
        request = prepare_local_voice_audition(
            get_settings(), args.source, args.output, segment_ids=args.segment, feedback_ids=args.feedback)
        request_path = args.output.resolve() / 'request.json'
        from atnos.services.qwen_tts_local_runtime_service import audition_scope_sha256
        result = {'status': 'DRAFT_NOT_AUTHORIZED', 'request_path': str(request_path),
                  'request_sha256': hashlib.sha256(request_path.read_bytes()).hexdigest(),
                  'scope_sha256': audition_scope_sha256(request), 'new_inference_calls': 0}
    else:
        if args.segment or args.feedback:
            parser.error('--segment and --feedback only prepare a local draft')
        if args.comparison == 'local-interpretation' and not args.request_sha256:
            parser.error('Local execution requires --request-sha256; prepare a draft first')
        result = asyncio.run(create_voice_audition(
            get_settings(), args.source, args.output, comparison=args.comparison,
            request_sha256=args.request_sha256))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
