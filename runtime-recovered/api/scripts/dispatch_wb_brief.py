"""Dispatch one local WB order from a JSON brief. Does not publish."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atnos.core.config import get_settings
from atnos.production.order_dispatch import ProductionOrderDispatcher
from atnos.production.order_store import ProductionOrderStore
from atnos.production.project_profile import ProductionOrder

brief = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
settings = get_settings()
store = ProductionOrderStore(settings)
state = store.create(
    ProductionOrder(
        topic=brief["topic"],
        direction=brief["direction"],
        optional_notes=brief["notes"],
        delivery_mode="local_only",
    ),
    request_key=brief["request_key"],
)
result = ProductionOrderDispatcher(settings).dispatch(state["id"])
print(json.dumps({"id": result.get("id"), "status": result.get("status"), "step": result.get("current_step"), "worker_requested": result.get("worker_requested"), "launch_pid": result.get("launch_pid")}, ensure_ascii=True))
