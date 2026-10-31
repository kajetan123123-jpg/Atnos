"""Lokalny knowledge-graph pamięci (kompatybilny z kształtem MCP memory).

Przechowuje entities / relations / observations w JSON na E: —
bez zależności od zewnętrznego serwera MCP.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from threading import Lock
from typing import Any
from uuid import uuid4

from atnos.core.config import Settings


@dataclass
class MemoryEntity:
    name: str
    entity_type: str
    observations: list[str] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryRelation:
    from_name: str
    to_name: str
    relation_type: str
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemoryGraphService:
    """Prosty graf pamięci: entity → observations, entity→entity relations."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        root = Path(settings.data_dir).expanduser().resolve() / "memory-graph"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "graph.json"
        self._lock = Lock()

    def _now(self) -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"entities": {}, "relations": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"entities": {}, "relations": []}
        if not isinstance(payload, dict):
            return {"entities": {}, "relations": []}
        entities = payload.get("entities") if isinstance(payload.get("entities"), dict) else {}
        relations = payload.get("relations") if isinstance(payload.get("relations"), list) else []
        return {"entities": entities, "relations": relations}

    def _save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def remember(
        self,
        *,
        name: str,
        entity_type: str = "fact",
        observation: str,
    ) -> dict[str, Any]:
        clean_name = " ".join(str(name or "").strip().split())
        clean_obs = " ".join(str(observation or "").strip().split())
        clean_type = " ".join(str(entity_type or "fact").strip().split()) or "fact"
        if not clean_name or not clean_obs:
            raise ValueError("name and observation are required")
        with self._lock:
            payload = self._load()
            entities: dict[str, Any] = payload["entities"]
            key = clean_name.casefold()
            current = entities.get(key) if isinstance(entities.get(key), dict) else None
            observations = list(current.get("observations") or []) if current else []
            if clean_obs not in observations:
                observations.append(clean_obs)
            entities[key] = {
                "name": clean_name if not current else str(current.get("name") or clean_name),
                "entity_type": clean_type if not current else str(current.get("entity_type") or clean_type),
                "observations": observations[-40:],
                "updated_at": self._now(),
            }
            payload["entities"] = entities
            self._save(payload)
            return {"status": "remembered", "entity": entities[key]}

    def relate(
        self,
        *,
        from_name: str,
        to_name: str,
        relation_type: str,
    ) -> dict[str, Any]:
        left = " ".join(str(from_name or "").strip().split())
        right = " ".join(str(to_name or "").strip().split())
        rel = " ".join(str(relation_type or "").strip().split())
        if not left or not right or not rel:
            raise ValueError("from_name, to_name and relation_type are required")
        with self._lock:
            payload = self._load()
            relations: list[Any] = list(payload.get("relations") or [])
            duplicate = any(
                isinstance(item, dict)
                and str(item.get("from_name") or "").casefold() == left.casefold()
                and str(item.get("to_name") or "").casefold() == right.casefold()
                and str(item.get("relation_type") or "").casefold() == rel.casefold()
                for item in relations
            )
            if not duplicate:
                relations.append(
                    {
                        "id": uuid4().hex[:12],
                        "from_name": left,
                        "to_name": right,
                        "relation_type": rel,
                        "created_at": self._now(),
                    }
                )
            payload["relations"] = relations[-500:]
            # Ensure both ends exist as entities.
            entities: dict[str, Any] = payload["entities"]
            for name in (left, right):
                key = name.casefold()
                if key not in entities:
                    entities[key] = {
                        "name": name,
                        "entity_type": "node",
                        "observations": [],
                        "updated_at": self._now(),
                    }
            payload["entities"] = entities
            self._save(payload)
            return {"status": "related", "from": left, "to": right, "relation_type": rel}

    def search(self, query: str, *, limit: int = 12) -> dict[str, Any]:
        needle = " ".join(str(query or "").strip().casefold().split())
        if not needle:
            return {"query": query, "count": 0, "hits": []}
        tokens = [t for t in re.split(r"[^\wąćęłńóśźż]+", needle, flags=re.IGNORECASE) if t]
        with self._lock:
            payload = self._load()
        hits: list[dict[str, Any]] = []
        for entity in (payload.get("entities") or {}).values():
            if not isinstance(entity, dict):
                continue
            hay = " ".join(
                [
                    str(entity.get("name") or ""),
                    str(entity.get("entity_type") or ""),
                    " ".join(str(item) for item in (entity.get("observations") or [])),
                ]
            ).casefold()
            score = 0
            if needle and needle in hay:
                score += 5
            for token in tokens:
                if token and token in hay:
                    score += 1
            if score <= 0:
                continue
            related = [
                item
                for item in (payload.get("relations") or [])
                if isinstance(item, dict)
                and (
                    str(item.get("from_name") or "").casefold() == str(entity.get("name") or "").casefold()
                    or str(item.get("to_name") or "").casefold() == str(entity.get("name") or "").casefold()
                )
            ][:8]
            hits.append({"score": score, "entity": entity, "relations": related})
        hits.sort(key=lambda item: (-int(item["score"]), str(item["entity"].get("name") or "").casefold()))
        clipped = hits[: max(1, min(int(limit), 40))]
        return {"query": query, "count": len(clipped), "hits": clipped, "path": str(self.path)}

    def snapshot(self, *, limit_entities: int = 40) -> dict[str, Any]:
        with self._lock:
            payload = self._load()
        entities = list((payload.get("entities") or {}).values())
        entities = [item for item in entities if isinstance(item, dict)]
        entities.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return {
            "entity_count": len(entities),
            "relation_count": len(payload.get("relations") or []),
            "entities": entities[:limit_entities],
            "path": str(self.path),
        }
