"""Inspect the built interface against canonical read-only production responses."""
import json
import os
from pathlib import Path
import re

root = Path('E:/Atnos/App/reports/readiness-20260909/story-browser')
root.mkdir(parents=True, exist_ok=True)
profile = root / 'profile-home'
os.environ.update(USERPROFILE=str(profile), APPDATA=str(profile / 'AppData/Roaming'),
                  LOCALAPPDATA=str(profile / 'AppData/Local'), TEMP='E:/Atnos/Temp', TMP='E:/Atnos/Temp')
from playwright.sync_api import sync_playwright

report = {'scope': 'Built frontend with read-only canonical production endpoints; not a desktop authentication or publishing test.',
          'known_harness_limitations': ['The full /ui/bootstrap lifecycle is not mounted; the frontend may show a bootstrap error toast.',
                                       'Creation and feedback submission are tested separately; this browser harness blocks all writes.'],
          'page_errors': [], 'http_errors': [], 'checks': []}
with sync_playwright() as playwright:
    context = playwright.chromium.launch_persistent_context(str(root / 'chromium-profile'),
        executable_path='E:/Atnos/Storage/Tools/playwright-browsers/chromium-1217/chrome-win64/chrome.exe',
        headless=True, viewport={'width': 1440, 'height': 1000})
    page = context.pages[0]
    page.on('pageerror', lambda exc: report['page_errors'].append(str(exc)))
    page.on('response', lambda response: report['http_errors'].append({'url': response.url, 'status': response.status}) if response.status >= 400 else None)
    try:
        page.goto('http://127.0.0.1:3000/create')
        page.get_by_role('button', name=re.compile('Bajki dla dzieci')).click()
        page.get_by_label('Temat lub pomysł').fill('Mały lis odnajduje zgubioną latarenkę')
        assert page.get_by_role('button', name='Przygotuj scenariusz').is_enabled()
        assert page.get_by_text('Sześć scen po angielsku').is_visible()
        for width in (1440, 390):
            page.set_viewport_size({'width': width, 'height': 1000})
            overflow = page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
            assert not overflow
            page.screenshot(path=str(root / f'create-{width}.png'), full_page=True)
            report['checks'].append({'screen': 'story_creation', 'width': width, 'overflow': overflow, 'script_button_enabled': True})
        page.set_viewport_size({'width': 1440, 'height': 1000})
        page.goto('http://127.0.0.1:3000/studio/prod-0a5609e653ad')
        page.get_by_text('Pakiet gotowy do oceny', exact=True).wait_for()
        page.locator('video').wait_for()
        page.wait_for_function('document.querySelector("video").readyState >= 1')
        report['finance_video_duration'] = page.locator('video').evaluate('(video) => video.duration')
        page.screenshot(path=str(root / 'finance-ready.png'), full_page=True)
        orders = page.request.get('http://127.0.0.1:8000/api/production/orders').json()['orders']
        ready = [order for order in orders if order['status'] == 'script_ready']
        report['ready_story_ids'] = [order['id'] for order in ready]
        for order in ready:
            review = page.request.get('http://127.0.0.1:8000/api/production/orders/' + order['id'] + '/review-package').json()
            current_blockers = review.get('blocking_failures', [])
            page.goto('http://127.0.0.1:3000/studio/' + order['id'])
            page.get_by_role('heading', name='Scenariusz opowieści').wait_for()
            banner = 'Wymaga decyzji lub poprawki' if current_blockers else 'Scenariusz gotowy do przeczytania'
            page.get_by_text(banner, exact=True).last.wait_for()
            if current_blockers:
                assert page.get_by_text('Scenariusz wymaga poprawki', exact=True).is_visible()
                assert page.get_by_text('Scenariusz gotowy do przeczytania', exact=True).count() == 0
                assert page.get_by_text('Scenariusz ukończony; gotowy do przeczytania', exact=True).count() == 0
            assert page.get_by_role('heading', name=re.compile('Scena [1-6]')).count() == 6
            assert page.locator('video').count() == 0
            for width in (1440, 390):
                page.set_viewport_size({'width': width, 'height': 1000})
                overflow = page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
                assert not overflow
                page.screenshot(path=str(root / f"{order['request']['project']}-{order['id']}-{width}.png"), full_page=True)
                report['checks'].append({'screen': 'story_preview', 'job_id': order['id'], 'width': width,
                                         'overflow': overflow, 'scenes': 6, 'video_element': False,
                                         'current_blockers': current_blockers, 'banner': banner})
        assert not report['page_errors']
        report['status'] = 'passed'
    except Exception as exc:
        report['status'] = 'failed'
        report['error_type'] = type(exc).__name__
        raise
    finally:
        (root / 'inspection.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        context.close()
print(json.dumps(report))
