from pathlib import Path
import pytest
from pydantic import ValidationError
from atnos.production.project_manifest import BrandSpec
from atnos.production.channels.wealth_blueprint import preview
from types import SimpleNamespace

def test_grouping_cannot_drop_rewrite_or_reorder_narration():
    script={'segments':[{'text':'First thought.'},{'text':'Second thought.'},{'text':'Follow WB.'}]}
    manifest=SimpleNamespace(scenes=[SimpleNamespace(narration='First thought. Second thought.'),SimpleNamespace(narration='Follow WB.')])
    assert preview.comparison_narration_matches(manifest,script)
    manifest.scenes[0].narration='Second thought. First thought.'
    assert not preview.comparison_narration_matches(manifest,script)
    manifest.scenes[0].narration='First thought.'
    assert not preview.comparison_narration_matches(manifest,script)

def test_no_opt_in_preserves_legacy_brand_payload():
    assert 'comparisonStyle' not in BrandSpec().model_dump(mode='json')
    for style in preview.STYLE_COMPARISON_PRESETS:
        assert BrandSpec(comparisonStyle=style).comparisonStyle == style
    with pytest.raises(ValidationError):
        BrandSpec(comparisonStyle='unregistered')

def test_unknown_style_and_escaped_output_stop_before_runtime(monkeypatch):
    monkeypatch.setattr(preview, 'get_settings', lambda: pytest.fail('No runtime expected'))
    with pytest.raises(ValueError,match='Unknown'):
        preview.render_style_comparison(Path('E:/source'),Path('E:/output'),style_id='unregistered')
    with pytest.raises(ValueError,match='separate'):
        preview.render_style_comparison(Path('E:/source'),Path('E:/source/output'),style_id='painted_editorial')

