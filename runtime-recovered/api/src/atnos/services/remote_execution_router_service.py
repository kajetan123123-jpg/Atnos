from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from sqlalchemy.orm import Session

from atnos.db.models.enums import RemoteRoutingTarget
from atnos.repositories.remote_node_repository import RemoteNodeRepository


WINDOWS_PATH_PATTERN = re.compile(r"\b[A-Za-z]:\\")


@dataclass(frozen=True, slots=True)
class RemoteRouteDecision:
    target: RemoteRoutingTarget
    node_id: int | None
    reason: str


class RemoteExecutionRouterService:
    WINDOWS_ONLY_TASKS = frozenset(
        {
            "windows_gui",
            "windows_desktop",
            "windows_file",
            "windows_browser",
        }
    )
    REMOTE_PREFERRED_TASKS = frozenset(
        {
            "shell",
            "service",
            "docker",
            "browser",
            "research",
            "maintenance",
            "ollama",
            "conversion",
            "document",
        }
    )

    def __init__(self, session: Session) -> None:
        self.session = session
        self.remote_nodes = RemoteNodeRepository(session)

    def route(
        self,
        *,
        task_kind: str,
        payload: dict[str, Any],
        preferred_node_id: int | None = None,
    ) -> RemoteRouteDecision:
        normalized_task_kind = task_kind.strip().lower() or "shell"
        policy = self.remote_nodes.get_routing_policy(normalized_task_kind)

        if preferred_node_id is not None:
            preferred = self.remote_nodes.get_node(preferred_node_id)
            if preferred is not None and preferred.status.value == "connected" and not preferred.pause_requested:
                return RemoteRouteDecision(
                    target=RemoteRoutingTarget.REMOTE,
                    node_id=preferred.id,
                    reason=f"Preferred remote node '{preferred.label}' is healthy.",
                )

        if policy is not None:
            if policy.windows_only:
                return RemoteRouteDecision(
                    target=RemoteRoutingTarget.LOCAL,
                    node_id=None,
                    reason=f"Task kind '{normalized_task_kind}' is pinned to Windows by routing policy.",
                )
            if not policy.remote_enabled:
                return RemoteRouteDecision(
                    target=RemoteRoutingTarget.LOCAL,
                    node_id=None,
                    reason=f"Remote execution is disabled for task kind '{normalized_task_kind}'.",
                )

        if normalized_task_kind in self.WINDOWS_ONLY_TASKS or self._payload_requires_windows(payload):
            return RemoteRouteDecision(
                target=RemoteRoutingTarget.LOCAL,
                node_id=None,
                reason="Task references Windows-local paths, GUI, or host-bound context.",
            )

        candidates = [
            node
            for node in self.remote_nodes.list_connected_nodes()
            if not node.pause_requested and node.revoked_at is None
        ]
        if not candidates:
            return RemoteRouteDecision(
                target=RemoteRoutingTarget.LOCAL,
                node_id=None,
                reason="No connected remote worker is currently available.",
            )

        node = candidates[0]
        snapshot = self.remote_nodes.latest_capability_snapshot(node.id)
        if snapshot is None:
            return RemoteRouteDecision(
                target=RemoteRoutingTarget.REMOTE,
                node_id=node.id,
                reason=f"Remote node '{node.label}' is connected and awaiting its first capability snapshot.",
            )
        if normalized_task_kind == "browser" and not snapshot.browser_ready:
            return RemoteRouteDecision(
                target=RemoteRoutingTarget.LOCAL,
                node_id=None,
                reason=f"Remote node '{node.label}' has no browser bridge ready.",
            )
        if normalized_task_kind in {"service", "docker", "shell"} and not snapshot.sudo_available:
            return RemoteRouteDecision(
                target=RemoteRoutingTarget.LOCAL,
                node_id=None,
                reason=f"Remote node '{node.label}' has no sudo capability available.",
            )
        if policy is not None and policy.preferred_target == RemoteRoutingTarget.LOCAL:
            return RemoteRouteDecision(
                target=RemoteRoutingTarget.LOCAL,
                node_id=None,
                reason=f"Task kind '{normalized_task_kind}' is currently preferred locally by policy.",
            )
        if normalized_task_kind not in self.REMOTE_PREFERRED_TASKS and policy is None:
            return RemoteRouteDecision(
                target=RemoteRoutingTarget.LOCAL,
                node_id=None,
                reason=f"Task kind '{normalized_task_kind}' is not in the default remote-safe matrix.",
            )
        return RemoteRouteDecision(
            target=RemoteRoutingTarget.REMOTE,
            node_id=node.id,
            reason=f"Remote node '{node.label}' is healthy and matches the remote execution policy.",
        )

    @classmethod
    def _payload_requires_windows(cls, payload: dict[str, Any]) -> bool:
        haystack = " ".join(
            [
                str(payload.get("label") or ""),
                str(payload.get("command") or ""),
                str(payload.get("path") or ""),
                str(payload.get("workdir") or ""),
                str(payload.get("browser_target") or ""),
                str(payload),
            ]
        )
        return bool(WINDOWS_PATH_PATTERN.search(haystack))
