from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import time

import psutil

import uvicorn


def _selector_loop_factory() -> asyncio.AbstractEventLoop:
    """Utwórz stabilną pętlę serwera dla Windows i Pythona 3.14."""
    return asyncio.SelectorEventLoop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Uruchom lokalne API Atnosa.")
    parser.add_argument("--app", default="atnos.api.main:create_app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args()


async def serve_with_graceful_control(server, request_path: Path) -> None:
    """A local, identity-bound request drains Uvicorn; never force-kill streams."""
    created = psutil.Process().create_time()

    async def watch_shutdown():
        while not server.should_exit:
            await asyncio.sleep(0.5)
            try:
                payload = json.loads(request_path.read_text(encoding="utf-8"))
                if (payload.get("pid") == os.getpid()
                    and abs(float(payload.get("created", 0)) - created) < .001
                    and float(payload.get("expires", 0)) > time.time()):
                    request_path.unlink(missing_ok=True)
                    server.should_exit = True
            except (OSError, ValueError, TypeError):
                continue

    watcher = asyncio.create_task(watch_shutdown())
    try:
        await server.serve()
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


def main() -> None:
    args = parse_args()
    os.environ['ATNOS_RESTART_PROTOCOL'] = 'graceful-v1'
    config = uvicorn.Config(
        args.app,
        factory=True,
        host=args.host,
        port=args.port,
        loop=_selector_loop_factory,
        # Uvicorn's access logger persists the full query string. OAuth callback
        # URLs contain one-time authorization codes and state values.
        access_log=False,
    )
    data = Path(os.environ.get("DATA_DIR") or r"E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data")
    with asyncio.Runner(loop_factory=_selector_loop_factory) as runner:
        runner.run(serve_with_graceful_control(uvicorn.Server(config), data / "runtime/api-shutdown-request.json"))


if __name__ == "__main__":
    main()
