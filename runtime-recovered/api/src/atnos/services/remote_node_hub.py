from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import WebSocket


class RemoteNodeHubError(RuntimeError):
    pass


@dataclass
class ConnectedRemoteNode:
    node_id: int
    websocket: "WebSocket"


class RemoteNodeHub:
    def __init__(self) -> None:
        self._connections: dict[int, ConnectedRemoteNode] = {}
        self._lock = asyncio.Lock()

    async def connect(self, node_id: int, websocket: "WebSocket") -> None:
        async with self._lock:
            self._connections[node_id] = ConnectedRemoteNode(node_id=node_id, websocket=websocket)

    async def disconnect(self, node_id: int) -> None:
        async with self._lock:
            self._connections.pop(node_id, None)

    async def send(self, node_id: int, frame: dict[str, object]) -> None:
        async with self._lock:
            connection = self._connections.get(node_id)
        if connection is None:
            raise RemoteNodeHubError(f"Remote node {node_id} is not connected.")
        await connection.websocket.send_json(frame)

    async def has_connection(self, node_id: int) -> bool:
        async with self._lock:
            return node_id in self._connections

    async def receive_loop(
        self,
        node_id: int,
        websocket: "WebSocket",
        *,
        handler: Callable[[int, dict[str, object]], Awaitable[None]],
    ) -> None:
        await self.connect(node_id, websocket)
        try:
            while True:
                message = await websocket.receive_json()
                if not isinstance(message, dict):
                    continue
                await handler(node_id, message)
        finally:
            await self.disconnect(node_id)
