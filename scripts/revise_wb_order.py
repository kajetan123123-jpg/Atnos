"""Register operator defects and let Atnos revise an existing local WB order."""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime-recovered/api/src'))
from atnos.core.config import get_settings
from atnos.core.logging import redact_secrets
from atnos.production.scoped_script_revision import register_feedback, run_revision, authorize_feedback_rounds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('register', 'run', 'extend-rounds'))
    parser.add_argument('job_id')
    parser.add_argument('--feedback', type=Path)
    parser.add_argument('--authorized', action='store_true')
    parser.add_argument('--followup', action='store_true')
    parser.add_argument('--continue-rejected', action='store_true')
    parser.add_argument('--maximum-rounds', type=int)
    parser.add_argument('--reason')
    args = parser.parse_args()
    settings = get_settings()
    if args.command == 'extend-rounds':
        if args.maximum_rounds is None or not args.reason:
            parser.error('A bounded maximum and concrete authorization reason are required')
        result = authorize_feedback_rounds(settings, args.job_id, args.maximum_rounds,
                                          reason=args.reason, approved=args.authorized)
    elif args.command == 'register':
        if not args.feedback or args.feedback.resolve().drive.upper() != 'E:':
            parser.error('Feedback must be saved on E:')
        result = register_feedback(settings, args.job_id, json.loads(args.feedback.read_text('utf-8-sig')), approved=args.authorized, followup=args.followup)
    else:
        result = asyncio.run(run_revision(settings, args.job_id, continue_rejected=args.continue_rejected, approved=args.authorized))
    print(json.dumps(redact_secrets(result), ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
