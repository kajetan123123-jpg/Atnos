from __future__ import annotations

import math
from typing import Protocol, Sequence

from atnos.production.asset_brain.models import AssetQuery, AssetRecord


class EmbeddingClient(Protocol):
    def status(self, model: str) -> dict[str, object]: ...

    def embed(self, texts: Sequence[str], model: str) -> list[list[float]]: ...


def cosine(left: list[float], right: list[float]) -> float:
    """Same geometry as order_assets.rank_library — reuse embedding stack, not a second searcher."""
    if not left or len(left) != len(right) or not all(math.isfinite(x) for x in [*left, *right]):
        raise ValueError("Embedding vectors are empty, incompatible or non-finite")
    length = math.sqrt(sum(x * x for x in left) * sum(x * x for x in right))
    if length == 0:
        raise ValueError("Embedding vector has no semantic signal")
    return sum(x * y for x, y in zip(left, right, strict=True)) / length


def semantic_scores(
    *,
    assets: list[AssetRecord],
    query: AssetQuery,
    client: EmbeddingClient,
    model: str,
) -> dict[str, float]:
    text = (query.text or "").strip()
    if not text or not assets:
        return {}
    client.status(model)
    corpus = [asset.searchable_text() for asset in assets]
    vectors = client.embed([*corpus, text], model)
    query_vector = vectors[-1]
    scores: dict[str, float] = {}
    for asset, vector in zip(assets, vectors[:-1], strict=True):
        scores[asset.id] = round(cosine(vector, query_vector), 6)
    return scores
