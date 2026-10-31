from types import SimpleNamespace
import pytest
from atnos.production.project_manifest import save_project_manifest
from atnos.production.steps.order_delivery import _remotion_scene_targets
from test_semantic_qa_remotion_scene_gate import _minimal_manifest

def test_qa_rejects_standard_render_for_frozen_educational_request(tmp_path):
    video=tmp_path/'video.mp4';video.write_bytes(b'isolated offline fixture')
    manifest=tmp_path/'project_manifest.json'
    save_project_manifest(manifest,_minimal_manifest('fixture',[('s01',0,7)]))
    context=SimpleNamespace(artifact_dir=tmp_path,artifacts={
        'edit':{'manifest_path':str(manifest),'output_file':str(video)},
        'project_profile':{'visual_presentation':'educational_illustration','production':{}},
    })
    with pytest.raises(ValueError,match='presentation differs'):
        _remotion_scene_targets(context)
