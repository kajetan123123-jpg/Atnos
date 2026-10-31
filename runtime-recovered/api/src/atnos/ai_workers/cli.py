from __future__ import annotations

import argparse
import asyncio
import json

from atnos.ai_workers.delegation_router import AiDelegationRouter, DelegateTaskInput
from atnos.core.config import get_settings
from atnos.db.session import DatabaseManager


async def _delegate(args: argparse.Namespace) -> None:
    settings = get_settings()
    database = DatabaseManager(settings)
    database.initialize()
    context = json.loads(args.context) if args.context else {}
    with database.session_scope() as session:
        result = await AiDelegationRouter(session, settings).delegate(
            DelegateTaskInput(
                task_type=args.task_type,
                prompt=args.prompt,
                context=context if isinstance(context, dict) else {"value": context},
                preferred_worker=args.worker,
                allowed_workers=tuple(args.allowed_worker or ()),
                privacy_mode=args.privacy_mode,
                priority=args.priority,
                requested_by="cli",
                requires_approval_for_result=args.requires_approval,
            )
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Atnos AI worker delegation CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    delegate = subparsers.add_parser("delegate", help="Delegate a prompt to an AI worker")
    delegate.add_argument("--task-type", required=True)
    delegate.add_argument("--prompt", required=True)
    delegate.add_argument("--context", default="")
    delegate.add_argument("--worker", default=None)
    delegate.add_argument("--allowed-worker", action="append")
    delegate.add_argument("--privacy-mode", default="strict_local")
    delegate.add_argument("--priority", default="normal")
    delegate.add_argument("--requires-approval", action="store_true")
    args = parser.parse_args()
    if args.command == "delegate":
        asyncio.run(_delegate(args))


if __name__ == "__main__":
    main()
