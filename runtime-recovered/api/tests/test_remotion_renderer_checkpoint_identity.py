"""Normal render caching must track actual source and caption dependency changes."""
from pathlib import Path

import pytest

from atnos.production import remotion_renderer
from atnos.production.channels.wealth_blueprint.remotion_order_edit import renderer_source_fingerprint
from atnos.production.channels.wealth_blueprint.remotion_order_edit import stabilize_manifest_revision_dates
from atnos.production.order_store import file_hash
from atnos.production.project_manifest import save_project_manifest
from atnos.production.project_profile import content_hash
from test_semantic_qa_remotion_scene_gate import _minimal_manifest


@pytest.fixture
def renderer_tree(tmp_path, monkeypatch):
    required = ("package.json", "pnpm-lock.yaml", "src/Root.tsx", "src/contracts.ts", "src/design.tsx",
                "src/scenes.tsx", "src/WealthBlueprintShort.tsx", "src/typography.tsx")
    for name in (*required, "src/motion/registry.ts", "src/dataviz/theme.ts", "src/VerifiedCompoundChart.tsx",
                 "src/WBPlanningDeskScene.tsx", "src/WBPlanningDeskHands.tsx", "src/WBPaintedMaterials.tsx",
                 "scripts/render-browser.ts", "tsconfig.json"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("const test = 'source v1';", encoding="utf-8")
    monkeypatch.setattr(remotion_renderer, "_renderer_font_assets", lambda: {"regular": {"sha256": "a" * 64}})
    return tmp_path


@pytest.mark.parametrize("changed", ["src/Root.tsx", "src/design.tsx", "src/contracts.ts", "src/scenes.tsx",
                                   "src/typography.tsx", "src/motion/registry.ts", "src/dataviz/theme.ts",
                                   "src/VerifiedCompoundChart.tsx", "src/WBPlanningDeskScene.tsx",
                                   "src/WBPlanningDeskHands.tsx", "src/WBPaintedMaterials.tsx", "scripts/render-browser.ts", "pnpm-lock.yaml"])
def test_source_change_invalidates_same_manifest_checkpoint(renderer_tree, changed):
    first = renderer_source_fingerprint(renderer_tree)
    inputs = {"manifest_sha256": "b" * 64, "renderer_source_fingerprint": first}
    assert renderer_source_fingerprint(renderer_tree) == first
    (renderer_tree / changed).write_text("const test = 'source v2';", encoding="utf-8")
    second = renderer_source_fingerprint(renderer_tree)
    assert second != first
    assert content_hash({**inputs, "renderer_source_fingerprint": second}) != content_hash(inputs)


def test_installed_modules_and_notes_are_excluded_from_source_identity(renderer_tree):
    first = renderer_source_fingerprint(renderer_tree)
    module = renderer_tree / "node_modules" / "dependency" / "index.ts"
    module.parent.mkdir(parents=True)
    module.write_text("not a renderer source", encoding="utf-8")
    (renderer_tree / "README.md").write_text("render notes", encoding="utf-8")
    assert renderer_source_fingerprint(renderer_tree) == first


def test_caption_font_change_invalidates_checkpoint(renderer_tree, monkeypatch):
    first = renderer_source_fingerprint(renderer_tree)
    monkeypatch.setattr(remotion_renderer, "_renderer_font_assets", lambda: {"regular": {"sha256": "c" * 64}})
    assert renderer_source_fingerprint(renderer_tree) != first


def test_removed_required_source_fails_closed_instead_of_reusing_cache(renderer_tree):
    (renderer_tree / "src" / "design.tsx").unlink()
    with pytest.raises(RuntimeError, match="source missing"):
        renderer_source_fingerprint(renderer_tree)


def test_identical_resume_keeps_exact_manifest_bytes_and_checkpoint_key(tmp_path):
    path = tmp_path / "project_manifest.json"
    previous = _minimal_manifest("stable-manifest", [("s01", 0, 2)])
    previous.created_at = previous.updated_at = "2026-10-01T12:00:00+00:00"
    save_project_manifest(path, previous)
    original_hash = file_hash(path)
    rebuilt = previous.model_copy(deep=True)
    rebuilt.created_at = rebuilt.updated_at = "2026-10-08T12:00:00+00:00"
    stable = stabilize_manifest_revision_dates(path, rebuilt)
    save_project_manifest(path, stable)
    assert stable.created_at == previous.created_at and stable.updated_at == previous.updated_at
    assert file_hash(path) == original_hash
    assert content_hash({"manifest_sha256": file_hash(path)}) == content_hash({"manifest_sha256": original_hash})


def test_real_scene_change_preserves_creation_and_uses_current_revision_date(tmp_path):
    path = tmp_path / "project_manifest.json"
    previous = _minimal_manifest("changed-manifest", [("s01", 0, 2)])
    previous.created_at = previous.updated_at = "2026-10-01T12:00:00+00:00"
    save_project_manifest(path, previous)
    original_hash = file_hash(path)
    rebuilt = previous.model_copy(deep=True)
    rebuilt.created_at = rebuilt.updated_at = "2026-10-08T12:00:00+00:00"
    rebuilt.scenes[0].headline = "A genuinely changed rendered headline"
    stable = stabilize_manifest_revision_dates(path, rebuilt)
    save_project_manifest(path, stable)
    assert stable.created_at == previous.created_at
    assert stable.updated_at == rebuilt.updated_at
    assert file_hash(path) != original_hash


def test_corrupted_previous_manifest_is_not_hidden_by_reconstruction(tmp_path):
    path = tmp_path / "project_manifest.json"
    manifest = _minimal_manifest("tampered-manifest", [("s01", 0, 2)])
    save_project_manifest(path, manifest)
    path.write_text(path.read_text(encoding="utf-8").replace("Headline s01", "Tampered headline"), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        stabilize_manifest_revision_dates(path, manifest)


def test_render_receipts_bind_the_new_board_in_both_entry_points():
    renderer = Path(__file__).resolve().parents[2] / 'remotion-renderer'
    assert 'src/ReserveReadinessScene.tsx' in remotion_renderer._RENDERER_SOURCE_FILES
    assert 'src/ReserveObjects.tsx' in remotion_renderer._RENDERER_SOURCE_FILES
    for source in ('src/WBPlanningDeskScene.tsx', 'src/WBPlanningDeskHands.tsx', 'src/WBPaintedMaterials.tsx'):
        assert source in remotion_renderer._RENDERER_SOURCE_FILES
    for entry in ('scripts/render.ts', 'scripts/render-browser.ts'):
        assert "'src/ReserveReadinessScene.tsx'" in (renderer / entry).read_text(encoding='utf-8')
        for source in ('src/WBPlanningDeskScene.tsx', 'src/WBPlanningDeskHands.tsx', 'src/WBPaintedMaterials.tsx'):
            assert repr(source) in (renderer / entry).read_text(encoding='utf-8')
