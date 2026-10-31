"""Create, inspect or resume a durable production order. Does not publish anything."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atnos.core.config import get_settings
from atnos.core.logging import redact_secrets
from atnos.production.order_dispatch import ProductionOrderDispatcher, order_status
from atnos.production.order_store import ProductionOrderStore
from atnos.production.project_profile import ProductionOrder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--topic", required=True)
    create.add_argument("--direction", default="")
    create.add_argument("--notes", default="")
    create.add_argument("--request-key")
    create.add_argument('--brand-kit', help='Immutable installed brand kit for a supervised local trial')
    create.add_argument('--visual-style-id', choices=('A', 'B', 'C', 'D'), help='Wealth Blueprint visual variant frozen with this order')
    create.add_argument('--voice-candidate', choices=('Roger-v3',), help='Selected candidate for this local WB trial only')
    create.add_argument("--queue-only", action="store_true")
    create.add_argument("--local-only", action="store_true", help="Exclude this order from publication, even when a project schedule is enabled")
    for name in ("status", "run", "resume", "recheck-scenes", "prepare-script"):
        sub.add_parser(name).add_argument("job_id")
    extend=sub.add_parser('extend-stage-attempts')
    extend.add_argument('job_id')
    extend.add_argument('stage')
    extend.add_argument('maximum',type=int)
    extend.add_argument('--reason',required=True)
    export=sub.add_parser('export-brand-trial')
    export.add_argument('job_id')
    export.add_argument('--destination',required=True)
    args = parser.parse_args()
    settings = get_settings()
    store = ProductionOrderStore(settings)
    if args.command == "create":
        state = store.create(ProductionOrder(topic=args.topic, direction=args.direction, optional_notes=args.notes,
            brand_kit=args.brand_kit,
            visual_style_id=args.visual_style_id,
            voice_candidate=args.voice_candidate,
            delivery_mode="local_only" if args.local_only else "project_policy"), request_key=args.request_key)
        result = order_status(state) if args.queue_only else ProductionOrderDispatcher(settings).dispatch(state["id"])
    elif args.command == 'export-brand-trial':
        from atnos.production.brand_delivery import export_brand_trial
        result=export_brand_trial(store,args.job_id,args.destination)
    elif args.command == 'extend-stage-attempts':
        result=order_status(store.authorize_stage_attempts(args.job_id,args.stage,args.maximum,reason=args.reason))
    elif args.command == "status":
        result = order_status(store.get(args.job_id))
    elif args.command == "resume":
        result = ProductionOrderDispatcher(settings).dispatch(args.job_id)
    elif args.command == "recheck-scenes":
        from atnos.production.steps.order_assets import revalidate_scene_plan
        result = order_status(revalidate_scene_plan(store, args.job_id))
    elif args.command == "prepare-script":
        from atnos.production.order_execution import run_order
        result = order_status(asyncio.run(run_order(settings, args.job_id, script_only=True)))
    else:
        from atnos.production.orchestrator import ProductionOrchestrator
        result = order_status(asyncio.run(ProductionOrchestrator(settings).run_order(args.job_id)))
    print(json.dumps(redact_secrets(result), ensure_ascii=True), flush=True)
    return 2 if args.command != "status" and not result.get("worker_requested") and result["status"] in {"blocked", "failed"} else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": redact_secrets(str(exc))}), flush=True)
        raise SystemExit(2)
