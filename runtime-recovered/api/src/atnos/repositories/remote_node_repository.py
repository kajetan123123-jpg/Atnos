from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.enums import (
    RemoteExecutionStatus,
    RemoteNodeStatus,
)
from atnos.db.models.remote_node import (
    RemoteExecutionRun,
    RemoteExecutionStep,
    RemoteNode,
    RemoteNodeCapabilitySnapshot,
    RemoteNodeCredential,
    RemoteNodeSession,
    RemoteRoutingPolicy,
)


class RemoteNodeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_node(self, **kwargs) -> RemoteNode:
        item = RemoteNode(**kwargs)
        self.session.add(item)
        self.session.flush()
        return item

    def save_node(self, item: RemoteNode) -> RemoteNode:
        self.session.add(item)
        self.session.flush()
        return item

    def get_node(self, node_id: int) -> RemoteNode | None:
        return self.session.get(RemoteNode, node_id)

    def list_nodes(self) -> list[RemoteNode]:
        statement = select(RemoteNode).order_by(RemoteNode.updated_at.desc(), RemoteNode.id.desc())
        return list(self.session.scalars(statement))

    def list_connected_nodes(self) -> list[RemoteNode]:
        statement = (
            select(RemoteNode)
            .where(RemoteNode.status == RemoteNodeStatus.CONNECTED)
            .order_by(RemoteNode.last_seen_at.desc().nullslast(), RemoteNode.id.desc())
        )
        return list(self.session.scalars(statement))

    def get_credential(self, node_id: int) -> RemoteNodeCredential | None:
        statement = select(RemoteNodeCredential).where(RemoteNodeCredential.node_id == node_id)
        return self.session.scalar(statement)

    def create_credential(self, **kwargs) -> RemoteNodeCredential:
        item = RemoteNodeCredential(**kwargs)
        self.session.add(item)
        self.session.flush()
        return item

    def save_credential(self, item: RemoteNodeCredential) -> RemoteNodeCredential:
        self.session.add(item)
        self.session.flush()
        return item

    def create_session(self, **kwargs) -> RemoteNodeSession:
        item = RemoteNodeSession(**kwargs)
        self.session.add(item)
        self.session.flush()
        return item

    def get_active_session(self, node_id: int) -> RemoteNodeSession | None:
        statement = (
            select(RemoteNodeSession)
            .where(
                RemoteNodeSession.node_id == node_id,
                RemoteNodeSession.is_active.is_(True),
            )
            .order_by(RemoteNodeSession.connected_at.desc(), RemoteNodeSession.id.desc())
        )
        return self.session.scalar(statement)

    def deactivate_sessions(self, node_id: int) -> None:
        now = datetime.now(UTC)
        for item in self.session.scalars(
            select(RemoteNodeSession).where(
                RemoteNodeSession.node_id == node_id,
                RemoteNodeSession.is_active.is_(True),
            )
        ):
            item.is_active = False
            item.disconnected_at = now
            self.session.add(item)
        self.session.flush()

    def save_session(self, item: RemoteNodeSession) -> RemoteNodeSession:
        self.session.add(item)
        self.session.flush()
        return item

    def create_capability_snapshot(self, **kwargs) -> RemoteNodeCapabilitySnapshot:
        item = RemoteNodeCapabilitySnapshot(**kwargs)
        self.session.add(item)
        self.session.flush()
        return item

    def latest_capability_snapshot(self, node_id: int) -> RemoteNodeCapabilitySnapshot | None:
        statement = (
            select(RemoteNodeCapabilitySnapshot)
            .where(RemoteNodeCapabilitySnapshot.node_id == node_id)
            .order_by(
                RemoteNodeCapabilitySnapshot.captured_at.desc(),
                RemoteNodeCapabilitySnapshot.id.desc(),
            )
        )
        return self.session.scalar(statement)

    def create_run(self, **kwargs) -> RemoteExecutionRun:
        item = RemoteExecutionRun(**kwargs)
        self.session.add(item)
        self.session.flush()
        return item

    def save_run(self, item: RemoteExecutionRun) -> RemoteExecutionRun:
        self.session.add(item)
        self.session.flush()
        return item

    def get_run(self, run_id: int) -> RemoteExecutionRun | None:
        return self.session.get(RemoteExecutionRun, run_id)

    def list_runs_for_node(self, node_id: int, *, limit: int | None = None) -> list[RemoteExecutionRun]:
        statement = (
            select(RemoteExecutionRun)
            .where(RemoteExecutionRun.assigned_node_id == node_id)
            .order_by(RemoteExecutionRun.updated_at.desc(), RemoteExecutionRun.id.desc())
        )
        if limit is not None:
            statement = statement.limit(max(1, limit))
        return list(self.session.scalars(statement))

    def list_recent_runs(self, *, limit: int | None = None) -> list[RemoteExecutionRun]:
        statement = select(RemoteExecutionRun).order_by(
            RemoteExecutionRun.updated_at.desc(),
            RemoteExecutionRun.id.desc(),
        )
        if limit is not None:
            statement = statement.limit(max(1, limit))
        return list(self.session.scalars(statement))

    def list_active_runs(self) -> list[RemoteExecutionRun]:
        statement = (
            select(RemoteExecutionRun)
            .where(
                RemoteExecutionRun.status.in_(
                    [
                        RemoteExecutionStatus.QUEUED,
                        RemoteExecutionStatus.DISPATCHING,
                        RemoteExecutionStatus.RUNNING,
                    ]
                )
            )
            .order_by(RemoteExecutionRun.updated_at.desc(), RemoteExecutionRun.id.desc())
        )
        return list(self.session.scalars(statement))

    def create_step(self, **kwargs) -> RemoteExecutionStep:
        item = RemoteExecutionStep(**kwargs)
        self.session.add(item)
        self.session.flush()
        return item

    def save_step(self, item: RemoteExecutionStep) -> RemoteExecutionStep:
        self.session.add(item)
        self.session.flush()
        return item

    def list_steps_for_run(self, run_id: int) -> list[RemoteExecutionStep]:
        statement = (
            select(RemoteExecutionStep)
            .where(RemoteExecutionStep.run_id == run_id)
            .order_by(RemoteExecutionStep.step_index.asc(), RemoteExecutionStep.id.asc())
        )
        return list(self.session.scalars(statement))

    def get_routing_policy(self, task_kind: str) -> RemoteRoutingPolicy | None:
        statement = select(RemoteRoutingPolicy).where(RemoteRoutingPolicy.task_kind == task_kind)
        return self.session.scalar(statement)

    def upsert_routing_policy(self, task_kind: str, **kwargs) -> RemoteRoutingPolicy:
        item = self.get_routing_policy(task_kind)
        if item is None:
            item = RemoteRoutingPolicy(task_kind=task_kind, **kwargs)
        else:
            for key, value in kwargs.items():
                setattr(item, key, value)
        self.session.add(item)
        self.session.flush()
        return item

    def list_routing_policies(self) -> list[RemoteRoutingPolicy]:
        statement = select(RemoteRoutingPolicy).order_by(RemoteRoutingPolicy.task_kind.asc())
        return list(self.session.scalars(statement))
