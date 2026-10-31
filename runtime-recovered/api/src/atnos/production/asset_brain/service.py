from __future__ import annotations

from pathlib import Path
from typing import Sequence

import httpx

from atnos.core.config import Settings
from atnos.production.asset_brain.catalog import catalog_path, default_library_root, load_catalog
from atnos.production.asset_brain.index import filter_assets, metadata_score
from atnos.production.asset_brain.models import (
    ASSET_TYPES,
    AssetCatalog,
    AssetMatch,
    AssetQuery,
    AssetRecord,
    AssetSelectionResult,
    AssetType,
    GenerationNeed,
)
from atnos.production.asset_brain.ranking import EmbeddingClient, semantic_scores
from atnos.services.semantic_knowledge_index_service import (
    OllamaEmbeddingClient,
    SemanticKnowledgeUnavailableError,
)


class WealthBlueprintAssetBrain:
    """Reusable asset library for Wealth Blueprint.

    SEARCH INTERNAL LIBRARY
      → if a suitable approved asset exists, return it
      → otherwise emit a generation need (Comfy not called here)
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        catalog: AssetCatalog | None = None,
        catalog_file: Path | None = None,
        library_root: Path | None = None,
        embedding_client: EmbeddingClient | None = None,
        embedding_model: str | None = None,
    ) -> None:
        self.settings = settings
        self.library_root = library_root or default_library_root()
        self.catalog_file = catalog_file or catalog_path(self.library_root)
        self.catalog = catalog or load_catalog(self.catalog_file)
        self.embedding_client = embedding_client
        if embedding_model:
            self.embedding_model = embedding_model
        elif settings is not None:
            self.embedding_model = settings.semantic_knowledge_embedding_model
        else:
            self.embedding_model = "bge-m3"

    def reload(self) -> AssetCatalog:
        self.catalog = load_catalog(self.catalog_file)
        return self.catalog

    def list_assets(
        self,
        *,
        type: AssetType | None = None,
        approved_only: bool = False,
    ) -> list[AssetRecord]:
        query = AssetQuery(
            channel=self.catalog.channel,
            type=type,
            require_approved=approved_only,
            limit=50,
        )
        return filter_assets(self.catalog, query)

    def get(self, asset_id: str) -> AssetRecord | None:
        for asset in self.catalog.assets:
            if asset.id == asset_id:
                return asset
        return None

    def search(self, query: AssetQuery) -> list[AssetMatch]:
        """Tag/metadata filter + optional semantic ranking via existing Ollama embeddings."""
        filtered = filter_assets(self.catalog, query)
        if not filtered:
            return []

        meta_rows: list[tuple[AssetRecord, float, list[str]]] = []
        for asset in filtered:
            score, reasons = metadata_score(asset, query)
            meta_rows.append((asset, score, reasons))

        semantic: dict[str, float] = {}
        if (query.text or "").strip():
            client = self._resolve_embedding_client()
            if client is not None:
                try:
                    semantic = semantic_scores(
                        assets=filtered,
                        query=query,
                        client=client,
                        model=self.embedding_model,
                    )
                except (SemanticKnowledgeUnavailableError, ValueError, httpx.HTTPError):
                    semantic = {}

        matches: list[AssetMatch] = []
        for asset, meta, reasons in meta_rows:
            sem = semantic.get(asset.id)
            if sem is None:
                score = meta
                method = "metadata"
                final_reasons = reasons
            else:
                score = round(min(1.0, 0.45 * meta + 0.55 * max(0.0, sem)), 6)
                method = "hybrid" if meta > 0 else "semantic"
                final_reasons = [*reasons, f"semantic:{sem:.4f}"]
            matches.append(
                AssetMatch(asset=asset, score=score, method=method, reasons=final_reasons)
            )

        matches.sort(key=lambda row: (-row.score, row.asset.id))
        return matches[: query.limit]

    def resolve_or_generate(self, query: AssetQuery, *, allow_generate: bool = True):
        """Search library, then optionally provision via Comfy stills (see provisioner)."""
        from atnos.production.asset_brain.provisioner import WealthBlueprintAssetProvisioner

        return WealthBlueprintAssetProvisioner(
            self.settings,
            brain=self,
            persist_catalog=True,
        ).resolve_or_generate(query, allow_generate=allow_generate)

    def select_best(self, query: AssetQuery) -> AssetSelectionResult:
        candidates = self.search(query)
        if candidates:
            best = candidates[0]
            threshold = query.min_semantic_score if (query.text or "").strip() else 0.2
            if best.score >= threshold:
                return AssetSelectionResult(status="found", match=best, candidates=candidates)

        suggested_type: AssetType = query.type or (query.types[0] if query.types else "icon")
        if suggested_type not in ASSET_TYPES:
            suggested_type = "icon"
        need = GenerationNeed(
            reason="No suitable approved asset in the Wealth Blueprint library",
            suggested_type=suggested_type,
            suggested_tags=list(dict.fromkeys([*query.tags_all, *query.tags_any])),
            suggested_character=query.character,
            suggested_pose=query.pose,
            suggested_emotion=query.emotion,
            suggested_direction=query.direction,
            query=query,
            candidates_considered=len(candidates),
        )
        return AssetSelectionResult(status="missing", generation_need=need, candidates=candidates)

    def resolve_file(self, asset: AssetRecord) -> Path:
        raw = str(asset.file_path or "").strip().replace("\\", "/")
        path = Path(raw)
        if path.is_absolute():
            if path.is_file():
                return path
            # Migrate legacy hardcoded App config paths → APP_ROOT/config/...
            normalized = raw.replace("\\", "/")
            marker = "/config/"
            if marker in normalized and self.settings is not None and self.settings.app_root is not None:
                relative = normalized.split(marker, 1)[1]
                candidate = (Path(self.settings.app_root) / "config" / relative).resolve()
                if candidate.is_file():
                    return candidate
            return path
        library_hit = (self.library_root / path).resolve()
        if library_hit.is_file():
            return library_hit
        if self.settings is not None and self.settings.app_root is not None:
            # Prefer APP_ROOT/config/<relative> for channel_references etc.
            config_hit = (Path(self.settings.app_root) / "config" / path).resolve()
            if config_hit.is_file():
                return config_hit
        return library_hit

    def _resolve_embedding_client(self) -> EmbeddingClient | None:
        if self.embedding_client is not None:
            return self.embedding_client
        if self.settings is None:
            return None
        # Asset selection immediately precedes Comfy generation. Expire this
        # request's own encoder after its response instead of leaving GPU
        # residency behind for the next production operation.
        return OllamaEmbeddingClient(self.settings.ollama_base_url, timeout_s=120, keep_alive=0)


def build_generation_need_from_slot(
    *,
    slot_type: AssetType,
    tags: Sequence[str] = (),
    character: str | None = None,
    pose: str | None = None,
    text: str = "",
) -> GenerationNeed:
    query = AssetQuery(
        type=slot_type,
        tags_any=list(tags),
        character=character,
        pose=pose,
        text=text,
    )
    return GenerationNeed(
        reason="Explicit generation need without library hit",
        suggested_type=slot_type,
        suggested_tags=list(tags),
        suggested_character=character,
        suggested_pose=pose,
        query=query,
        candidates_considered=0,
    )
