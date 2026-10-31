from __future__ import annotations

import asyncio
import json
from pathlib import Path

from atnos.jarvis.core.config import JarvisConfigStore

from .test_support import build_test_settings, make_temp_dir


class TestJarvisConfigStore:
    def test_hot_reload_publishes_updated_config(self) -> None:
        with make_temp_dir() as temp_dir:
            root = Path(temp_dir)
            settings = build_test_settings(root)
            store = JarvisConfigStore(settings)

            async def scenario() -> None:
                await store.start_hot_reload(interval_s=0.05)
                queue = await store.subscribe()
                initial = await asyncio.wait_for(queue.get(), timeout=0.2)
                assert initial.screen.interval_s == 2.0

                await asyncio.sleep(0.1)
                payload = json.loads(store.paths.config_path.read_text(encoding="utf-8"))
                payload["screen"]["interval_s"] = 3.5
                store.paths.config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

                updated = await asyncio.wait_for(queue.get(), timeout=1.0)
                assert updated.screen.interval_s == 3.5
                await store.stop_hot_reload()

            asyncio.run(scenario())
