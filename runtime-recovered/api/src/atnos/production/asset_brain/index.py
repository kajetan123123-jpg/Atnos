from __future__ import annotations

from collections import defaultdict

from atnos.production.asset_brain.models import AssetCatalog, AssetQuery, AssetRecord, AssetType


def _norm(value: str | None) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def index_by_type(catalog: AssetCatalog) -> dict[AssetType, list[AssetRecord]]:
    buckets: dict[AssetType, list[AssetRecord]] = defaultdict(list)
    for asset in catalog.assets:
        buckets[asset.type].append(asset)
    return dict(buckets)


def index_by_tag(catalog: AssetCatalog) -> dict[str, list[AssetRecord]]:
    buckets: dict[str, list[AssetRecord]] = defaultdict(list)
    for asset in catalog.assets:
        for tag in asset.tags:
            buckets[_norm(tag)].append(asset)
    return dict(buckets)


def index_by_character(catalog: AssetCatalog) -> dict[str, list[AssetRecord]]:
    buckets: dict[str, list[AssetRecord]] = defaultdict(list)
    for asset in catalog.assets:
        key = _norm(asset.character)
        if key:
            buckets[key].append(asset)
    return dict(buckets)


def filter_assets(catalog: AssetCatalog, query: AssetQuery) -> list[AssetRecord]:
    """Hard filters before optional semantic ranking."""
    channel = _norm(query.channel)
    wanted_types = set(query.types)
    if query.type:
        wanted_types.add(query.type)
    wanted_roles = set(query.production_roles)
    if query.production_role:
        wanted_roles.add(query.production_role)

    results: list[AssetRecord] = []
    for asset in catalog.assets:
        if channel and _norm(asset.channel) != channel:
            continue
        role = asset.production_role or "other"
        if query.exclude_reference_sheets and role == "reference_sheet":
            continue
        if wanted_roles and role not in wanted_roles:
            continue
        if query.require_approved and not query.allow_dev_unapproved:
            # Film final: APPROVED + non-reference production role.
            if not asset.is_production_selectable(allow_dev_unapproved=False):
                continue
        elif query.require_approved and query.allow_dev_unapproved:
            if asset.quality_status in {"REJECTED", "SUPERSEDED"}:
                continue
            if query.exclude_reference_sheets and role == "reference_sheet":
                continue
        if wanted_types and asset.type not in wanted_types:
            continue
        if query.character and _norm(asset.character) != _norm(query.character):
            continue
        if query.pose and _norm(asset.pose) != _norm(query.pose):
            continue
        if query.emotion and _norm(asset.emotion) != _norm(query.emotion):
            continue
        if query.direction and _norm(asset.direction) != _norm(query.direction):
            continue
        if query.version and _norm(asset.version) != _norm(query.version):
            continue
        if query.require_transparent is True and not asset.transparent_background:
            continue
        if query.require_transparent is False and asset.transparent_background:
            continue
        tags = {_norm(tag) for tag in asset.tags}
        if query.tags_all and not all(tag in tags for tag in (_norm(t) for t in query.tags_all)):
            continue
        if query.tags_any and not any(_norm(tag) in tags for tag in query.tags_any):
            continue
        results.append(asset)
    return results


def metadata_score(asset: AssetRecord, query: AssetQuery) -> tuple[float, list[str]]:
    """Deterministic non-embedding score for tag/metadata fit."""
    score = 0.0
    reasons: list[str] = []
    if query.type and asset.type == query.type:
        score += 0.25
        reasons.append("type")
    role = asset.production_role or "other"
    if query.production_role and role == query.production_role:
        score += 0.15
        reasons.append("production_role")
    if query.character and _norm(asset.character) == _norm(query.character):
        score += 0.2
        reasons.append("character")
    if query.pose and _norm(asset.pose) == _norm(query.pose):
        score += 0.15
        reasons.append("pose")
    if query.emotion and _norm(asset.emotion) == _norm(query.emotion):
        score += 0.1
        reasons.append("emotion")
    if query.direction and _norm(asset.direction) == _norm(query.direction):
        score += 0.1
        reasons.append("direction")
    if query.version and _norm(asset.version) == _norm(query.version):
        score += 0.05
        reasons.append("version")
    tags = {_norm(tag) for tag in asset.tags}
    any_hits = [_norm(tag) for tag in query.tags_any if _norm(tag) in tags]
    all_hits = [_norm(tag) for tag in query.tags_all if _norm(tag) in tags]
    if any_hits:
        score += min(0.2, 0.08 * len(any_hits))
        reasons.append(f"tags_any:{','.join(any_hits)}")
    if query.tags_all and len(all_hits) == len(query.tags_all):
        score += 0.15
        reasons.append("tags_all")
    if asset.is_production_selectable():
        score += 0.05
        reasons.append("approved_production")
    if query.text:
        hay = asset.searchable_text().casefold()
        tokens = [token for token in query.text.casefold().replace(",", " ").split() if len(token) > 2]
        hits = [token for token in tokens if token in hay]
        if hits:
            score += min(0.2, 0.04 * len(hits))
            reasons.append(f"text:{','.join(hits[:4])}")
    return round(min(1.0, score), 6), reasons
