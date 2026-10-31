import json

from atnos.intelligence.storage import connect
from atnos.production.workspace_catalog import build_workspace_catalog
from test_support import build_test_settings


def test_catalog_distinguishes_real_order_support_from_channel_ideas(tmp_path):
    settings = build_test_settings(tmp_path)
    with connect(settings) as db:
        db.execute("INSERT INTO intelligence_accounts(account_id,niches,region,last_requested_at) VALUES('yt-wealth-blueprint','[]','US','2026-09-09T00:00:00Z')")
        db.commit()
    catalog = build_workspace_catalog(settings)
    channels = {item['key']: item for item in catalog['channels']}
    assert set(channels) == {'wealth_blueprint', 'children_stories', 'horror_stories'}
    assert all(item['language'] == 'en' for item in channels.values())
    assert channels['wealth_blueprint']['can_order']
    assert not channels['children_stories']['can_order']
    assert not channels['horror_stories']['can_order']
    assert catalog['delivery_mode'] == 'local_only'


def test_invalid_saved_profile_does_not_enable_creation_or_expose_content(tmp_path):
    settings = build_test_settings(tmp_path)
    directory = settings.data_dir / 'production-profiles'
    directory.mkdir()
    (directory / 'wealth_blueprint.json').write_text(json.dumps({'private': 'must-not-leak'}))
    catalog = build_workspace_catalog(settings)
    assert not catalog['channels'][0]['can_order']
    assert catalog['channels'][0]['blockers']
    assert 'must-not-leak' not in json.dumps(catalog)


def test_profile_without_a_production_account_cannot_accept_orders(tmp_path):
    catalog = build_workspace_catalog(build_test_settings(tmp_path))
    assert not catalog['channels'][0]['can_order']
    assert any('kontem produkcji' in reason for reason in catalog['channels'][0]['blockers'])
